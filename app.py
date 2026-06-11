"""
app.py — dgk-product-scout 대시보드 (Streamlit).

사이드바 '화면 선택'으로 3개 화면 전환:
  · 차종 수요   — Phase C 차종 수요 스캐너(규모 C-3 + 추세 C-4). 단순 표시(등록 대조 없음).
  · 계절 제품   — 계절성(데이터랩) + 규모(검색광고 단일 키워드) 통합 랭킹.
  · 키워드 탐색기 — 시드 → 연관어 수확 → 키워드·검색량·경쟁도(사실만, 판정 없음).

실행: streamlit run app.py

표시 원칙: 규모·계절성·추세는 '별도 컬럼' — 단일 매력도 점수로 섞지 않는다(터미널 스크립트와 동일).
데이터 수집·분석 로직·임계값은 src/core·scripts 파이프라인 그대로 재사용(앱은 표시 레이어).

[표시 레이어 규약 — '<10']
  검색광고 API 는 극소 검색량을 정확한 수가 아니라 "< 10" 으로만 준다. 내부 계산은 이를
  config.LOW_VOLUME_FALLBACK(=0) 으로 보수 처리한다(합산·정렬·임계 불변). 표시에서만 그 0 을
  "<10" 으로 되살려 '값이 없는 게 아니라 극소'임을 보인다 — 임의 숫자로 채우지 않는다(없는 데이터 생성 금지).
"""

from __future__ import annotations

import os
import pathlib
import sys
from datetime import date, datetime, timedelta, timezone

import streamlit as st

try:
    # .env 의 네이버 API 키를 환경변수로 로드(없어도 무시 — 키 검증은 어댑터가 명시 처리).
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# scripts/ 의 터미널 파이프라인(차종 추세·계절 캘린더)을 재사용하기 위한 경로 추가.
# 표 포맷 로직을 앱에 중복 구현하지 않고 파이프라인 결과를 받아 st.dataframe 로 렌더한다.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "scripts"))

import config
import car_demand as car_cli  # scripts/car_demand.py — fetch_trends/_sort_rows/_trend_cell/_low_conf
import seasonal_calendar as sc  # scripts/seasonal_calendar.py — fetch_shape/fetch_single_volumes/analyze_shape
from src.adapters.naver_adapter import NaverAdapter
from src.core.car_demand import harvest_models, model_member_keywords, rank_models
from src.core.car_models import load_car_models
from src.core.teamp_mode import (
    fetch_blog_count,
    fetch_recent_blog_count,
    fetch_recent_3m_docs_partial,
    fetch_teamp_kw_rows_partial,
    format_recent_3m,
    format_recent_ratio,
    harvest_teamp_kw_items,
    top_gold_kw_rows,
    top_gold_kw_rows_by_ratio,
)
from src.core.search_volume import (
    FIELD_COMP_IDX,
    FIELD_MONTHLY_MOBILE,
    FIELD_MONTHLY_PC,
    dedupe_relkeywords,
    member_volume,
)

st.set_page_config(page_title="dgk-product-scout", layout="wide")


def _load_secrets_to_env() -> None:
    """Streamlit Cloud Secrets → os.environ 브리지.

    Streamlit Cloud 에서는 secrets.toml 키가 st.secrets 에 들어오지만
    코드베이스 곳곳의 os.environ.get(...)은 그걸 보지 못한다.
    앱 시작 시 한 번 호출해 두 공간을 동기화한다.
    로컬(secrets.toml 없음 / 키 없음)이면 조용히 건너뜀.

    ★TOML 섹션 주의: 네이버 키는 [auth] 섹션 '위'에 위치해야 최상위 키로 읽힌다.
      섹션 헤더 이후의 키는 해당 섹션에 속하므로 st.secrets["NAVER_AD_API_KEY"]가 아닌
      st.secrets["auth"]["NAVER_AD_API_KEY"]가 된다.
      이 함수는 올바른 위치(최상위)를 먼저 시도하고, 실수로 [auth] 아래 넣은 경우도 복구한다.
    """
    _NAVER_ENV_KEYS = (
        "NAVER_AD_API_KEY",
        "NAVER_AD_SECRET_KEY",
        "NAVER_AD_CUSTOMER_ID",
        "NAVER_CLIENT_ID",
        "NAVER_CLIENT_SECRET",
    )
    try:
        for key in _NAVER_ENV_KEYS:
            if os.environ.get(key):
                continue
            val = None
            # 1순위: 최상위(올바른 위치 — TOML에서 [auth] 위에 선언된 키)
            try:
                val = st.secrets[key]
            except KeyError:
                pass
            # 2순위: [auth] 아래(TOML 순서 실수로 [auth] 뒤에 넣었을 때 복구)
            if not val:
                try:
                    val = st.secrets["auth"][key]
                except (KeyError, AttributeError):
                    pass
            if val:
                os.environ[key] = str(val)
    except Exception:  # noqa: BLE001
        pass


def _password_gate() -> bool:
    """비밀번호 인증 게이트. 인증 전까지 앱 진입 차단.

    st.secrets["auth"]["password"] 가 없으면(로컬 개발 환경) 게이트를 건너뛴다.
    인증 성공 여부를 반환한다 — False 면 호출부에서 st.stop() 처리.
    """
    try:
        expected = st.secrets["auth"]["password"]
    except (KeyError, AttributeError):
        return True  # 로컬 환경 — 게이트 없음

    if st.session_state.get("_authenticated"):
        return True

    st.title("dgk-product-scout")
    pw = st.text_input("비밀번호", type="password", key="_pw_input")
    if st.button("로그인", key="_login_btn"):
        if pw == expected:
            st.session_state["_authenticated"] = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    return False


_load_secrets_to_env()
if not _password_gate():
    st.stop()


# ── UI 헬퍼 ──────────────────────────────────────────────────────────────────
_CSS = """<style>
.dgk-chip{background:#f8f9fa;border:0.5px solid #dee2e6;border-radius:8px;
          padding:10px 14px;text-align:center;margin-bottom:4px;}
.dgk-chip-label{font-size:11.5px;color:#6b7280;margin-bottom:2px;}
.dgk-chip-value{font-size:22px;font-weight:500;color:#111827;line-height:1.2;}
.dgk-hl{background:#fffbeb;border:1px solid #fcd34d;border-radius:12px;
        padding:14px 18px;margin-bottom:12px;}
.dgk-hl-title{font-size:13px;font-weight:500;color:#92400e;margin-bottom:6px;}
.dgk-pills{display:flex;flex-wrap:wrap;gap:6px;margin-top:4px;}
.dgk-pill{background:#fff;border:1px solid #fbbf24;border-radius:20px;
          padding:3px 10px;font-size:12.5px;color:#92400e;white-space:nowrap;}
.dgk-pill-sub{font-size:11px;color:#b45309;margin-left:3px;}
</style>"""


def _inject_css() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def _chips(items: list) -> None:
    """요약 칩 한 줄 — items = [(label, value_str), ...]"""
    cols = st.columns(len(items))
    for col, (label, value) in zip(cols, items):
        col.markdown(
            f'<div class="dgk-chip"><div class="dgk-chip-label">{label}</div>'
            f'<div class="dgk-chip-value">{value}</div></div>',
            unsafe_allow_html=True,
        )


def _highlight_card(title: str, pills: list) -> None:
    """amber 강조 카드 — pills = [(text, sub_text), ...]"""
    if not pills:
        return
    pills_html = "".join(
        f'<span class="dgk-pill">{p}'
        + (f'<span class="dgk-pill-sub">{s}</span>' if s else "")
        + "</span>"
        for p, s in pills
    )
    st.markdown(
        f'<div class="dgk-hl"><div class="dgk-hl-title">{title}</div>'
        f'<div class="dgk-pills">{pills_html}</div></div>',
        unsafe_allow_html=True,
    )


def _highlight_table(title: str, headers: list, rows_data: list, align: list = None) -> None:
    """amber 강조 카드 안에 소형 HTML 표.

    rows_data = list of cell-string lists per row.
    align = per-column "left"/"center"/"right" (기본 전부 left).
    """
    if not rows_data:
        return
    if align is None:
        align = ["left"] * len(headers)

    th_html = "".join(
        f'<th style="text-align:{a};padding:5px 10px;font-size:11.5px;font-weight:500;'
        f'color:#92400e;border-bottom:1.5px solid #fbbf24;white-space:nowrap;">{h}</th>'
        for h, a in zip(headers, align)
    )
    rows_html = ""
    for i, cells in enumerate(rows_data):
        bg = "#fffbeb" if i % 2 == 0 else "#fef9c3"
        tds = "".join(
            f'<td style="text-align:{a};padding:5px 10px;font-size:12.5px;'
            f'color:#1f2937;white-space:nowrap;">{c}</td>'
            for a, c in zip(align, cells)
        )
        rows_html += f'<tr style="background:{bg};">{tds}</tr>'

    table_html = (
        f'<table style="border-collapse:collapse;width:100%;margin-top:6px;">'
        f'<thead><tr style="background:#fef3c7;">{th_html}</tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        f'</table>'
    )
    st.markdown(
        f'<div class="dgk-hl"><div class="dgk-hl-title">{title}</div>{table_html}</div>',
        unsafe_allow_html=True,
    )


def _fmt_vol_display(vol: int, low: bool) -> str:
    """표시 전용 검색량 포맷. low(원래 '< 10')면 '<10', 아니면 천단위 콤마.
    ★숫자를 만들어내지 않는다 — '<10' 은 네이버가 정확값을 안 주는 항목의 표시일 뿐."""
    return "<10" if low else f"{vol:,}"


_KST = timezone(timedelta(hours=9))


def _kst_today() -> date:
    """오늘 날짜(KST). 발주 데드라인은 캐시(_load_seasonal)와 무관하게 매 렌더 현재 날짜로 계산."""
    return datetime.now(_KST).date()


def order_deadline_status(rising_month: int, today: date) -> tuple[date, str]:
    """수요 상승월(1~12) → (가장 가까운 미래 발주 마감일, 상태 텍스트).

    마감일 = 상승월 1일 − 리드타임(config.LEAD_TIME_TOTAL_DAYS, 89일).
    올해 마감이 이미 지났으면 '🔴 늦음' + 마감일은 내년 기준(가장 가까운 미래)을 반환.
    상태는 추천 필터와 무관하게 행마다 계산된다(필터 OFF 면 전 상품 상태가 보임).
    """
    total = config.LEAD_TIME_TOTAL_DAYS
    this_year = date(today.year, rising_month, 1) - timedelta(days=total)
    if this_year < today:                       # 올해 발주 적기 지남 → 늦음(내년 대비).
        next_year = date(today.year + 1, rising_month, 1) - timedelta(days=total)
        return next_year, "🔴 이미 늦음(내년 대비)"
    days = (this_year - today).days
    if days <= config.ORDER_STATUS_PRIME_DAYS:
        status = "🟢 지금 발주 적기"
    elif days <= config.ORDER_STATUS_SOON_DAYS:
        status = "🟡 임박"
    else:
        status = "⚪ 아직 이름"
    return this_year, status


# ═══════════════════════════════════════════════════════════════════════════
# 화면 1 — 차종 수요 (Phase C). 단순 표시(등록 대조 없음).
# ═══════════════════════════════════════════════════════════════════════════
# ★API 호출 캐싱: 클릭마다 검색광고+데이터랩을 재호출하면 느리고 rate limit 에 걸린다.
#   파이프라인 호출 전체를 1시간 캐시. '새로고침' 버튼으로 수동 무효화(아래 render 참조).
@st.cache_data(ttl=3600, show_spinner="차종 수요 수집 중 (검색광고 + 데이터랩)...")
def _load_car_demand():
    """파이프라인(src.core.car_demand) 호출 → (규모순 rows, 추세 dict, 안내문, members_map).

    표 포맷은 하지 않는다 — 파이프라인 결과(ModelRow/Trend)를 그대로 돌려주고
    렌더는 호출부가 st.dataframe 으로 한다(터미널 표 포맷 중복 없음)."""
    idx = load_car_models()
    seeds = config.CAR_PART_SEEDS
    adapter = NaverAdapter([s for ss in seeds.values() for s in ss])  # NAVER_AD_* 키 검증
    agg = harvest_models(adapter, seeds, idx)
    rows = rank_models(agg, idx, config.MODEL_MIN_VOLUME)

    # (검산용) (차종, 부품유형)별 묶인 키워드 원본을 그대로 보존 — 가공/필터 없음.
    # 파이프라인이 이미 agg["keywords"] 로 들고 있는 값 그대로(새 수집 아님). 캐시 안에 함께 보관.
    members_map = {key: list(a["keywords"]) for key, a in agg.items()}

    # 추세는 랭킹 모델 '전체'(중복 정규명 1회). 모델 단위로 데이터랩 그룹 묶음.
    canon_kw = model_member_keywords(agg)
    ranked_canons = {r.canonical for r in rows}
    canon_kw = {c: kw for c, kw in canon_kw.items() if c in ranked_canons}

    cid = os.environ.get("NAVER_CLIENT_ID", "").strip()
    csec = os.environ.get("NAVER_CLIENT_SECRET", "").strip()
    if not (cid and csec):
        return rows, {}, ("데이터랩 키(NAVER_CLIENT_ID/SECRET)가 .env 에 없어 추세를 "
                          "수집하지 못했습니다 — 추세 컬럼은 '–'로 표시됩니다."), members_map
    trends = car_cli.fetch_trends(canon_kw, cid, csec)  # scripts 의 데이터랩 수집 재사용
    return rows, trends, None, members_map


_LIMITS_MD = (
    "**[한계] — 표 해석 주의**\n\n"
    "① 추세는 '이미 검색량이 쌓인' 모델만 잡습니다 — 출시 직후 진짜 신차는 바닥이라 "
    "안 보입니다(그 구간은 시경의 시장감각이 데이터보다 빠름).\n\n"
    "② 데이터랩은 상대값 — 추세 '크기'는 모델 간 직접 비교 불가, '방향' 신호로만.\n\n"
    "③ 저신뢰(멤버1·규모 하한 근처) 모델은 방향만 표시(비율 숨김).\n\n"
    "· 규모(검색광고 절대값)와 추세(데이터랩)는 **별도 컬럼** — 합산/단일 점수 없음. "
    "'(세대미상)' 모호 버킷은 세대 판별이 필요합니다."
)

# 표시 전용 column_config — 사람이 읽는 헤더명(label) + ⓘ 툴팁(help). 데이터/로직/정렬 불변.
# dict 키(정규명·합산검색량 …)는 그대로 두고 표시명만 매핑한다. 세 뷰 모두 이 설정을 공유.
_DEMAND_COLUMN_CONFIG = {
    "정규명": st.column_config.TextColumn("차종"),
    "부품유형": st.column_config.TextColumn("부품"),
    # 천단위 콤마 + 숫자 정렬 유지(문자열로 굳히지 않음). 색칠·점수 없음.
    "합산검색량": st.column_config.NumberColumn(
        "월 검색량(PC+모바일)",
        format="localized",
        help="이 모델·부품에 묶인 연관 키워드들의 PC+모바일 월간 검색량 합계입니다. "
             "키워드 하나가 아니라 여러 개를 합산한 값입니다.",
    ),
    "멤버": st.column_config.NumberColumn(
        "묶인 키워드 수",
        format="localized",
        help="위 검색량 합산에 들어간 키워드 개수입니다. 1이면 키워드 하나에 전부 의존"
             "(값이 흔들릴 수 있음), 많을수록 여러 검색어에 걸쳐 더 단단한 수요입니다.",
    ),
    "추세": st.column_config.TextColumn(
        "수요 추세",
        help="최근 3개월이 과거 12개월 대비 얼마나 변했는지입니다. ▲ 증가 / ▼ 감소 / ― 보합. "
             "숫자는 증감%입니다. (저신뢰)가 붙은 행은 모델이 작아(키워드 1~3개) %가 노이즈일 "
             "수 있으니 방향 참고용으로만 보세요. 데이터랩 상대값이라 모델 간 추세 크기 비교는 "
             "불가 — 각 모델 내부 방향만 봅니다.",
    ),
    "신뢰도": st.column_config.TextColumn(
        "추세 신뢰도",
        help="정상 = 규모 충분(2000+)·키워드 2개+ 라 추세가 의미 있음. 저신뢰 = 규모가 작거나 "
             "키워드 1개라 추세가 노이즈일 수 있음 → 방향만 참고하고 숫자는 믿지 마세요.",
    ),
    "세대미상": st.column_config.TextColumn(
        "세대 구분",
        help="(세대미상)은 사람들이 세대코드 없이 검색한 경우입니다. 수요가 있다는 건 알지만 "
             "어느 세대(예: 카니발 KA4인지 YP인지)인지는 검색어에 정보가 없어 모릅니다. "
             "어느 세대 부품을 등록할지는 사람이 판단하는 칸입니다.",
    ),
}

# 묶인 키워드(검산) 작은 표 — 키워드 | 검색량(PC+모바일). 색칠·점수 없음.
# 검색량은 '<10' 표시를 위해 문자열(TextColumn). 표는 검색량 내림차순으로 미리 정렬해 둔다.
_MEMBER_COLUMN_CONFIG = {
    "키워드": st.column_config.TextColumn("키워드"),
    "검색량(PC+모바일)": st.column_config.TextColumn(
        "검색량(PC+모바일)",
        help="'<10'은 검색광고 API가 '< 10'으로만 주는 극소 검색량(정확값 비공개)입니다. "
             "본표 합산값은 여러 멤버 합이라 <10이 아닙니다.",
    ),
}


# ── 화면별 session_state 결과 캐시 키 ────────────────────────────────────────
_DEMAND_RESULTS_KEY = "_demand_results"
_SEASONAL_RESULTS_KEY = "_seasonal_results"
_TEAMP_RESULTS_KEY = "_teamp_results"


def _trend_display(t, low_conf: bool) -> str:
    """추세 표시 전용 포맷(렌더만). 화살표+증감%. 계산/임계값은 Trend(t) 그대로 사용 —
    방향(↑/↓/보합)은 compute_trend 가 이미 정한 값을 쓰고, %만 비율로 환산해 보여준다.

      신규 후보 → '신규 후보' · 데이터없음/계산불가 → '–'
      ↑(r≥1.1) → '▲ {pct}%' · ↓(r≤0.9) → '▼ {|pct|}%' · 보합 → '― 보합'
      저신뢰 행은 위 표기 뒤에 ' (저신뢰)' 꼬리.  ★색칠 없음(텍스트 문자만).
    """
    if t is None:
        return "–"
    if t.new_candidate:
        return "신규 후보"
    if t.ratio is None or t.data_insufficient or t.direction == "데이터부족":
        return "–"
    pct = round((t.ratio - 1) * 100)
    if t.direction == "↑":
        cell = f"▲ {pct}%"
    elif t.direction == "↓":
        cell = f"▼ {abs(pct)}%"
    else:  # 보합 (0.9 < r < 1.1)
        cell = "― 보합"
    return cell + (" (저신뢰)" if low_conf else "")


def _demand_table_rows(rows, trends):
    """ModelRow + Trend → st.dataframe 용 dict 리스트. 신뢰도는 터미널 헬퍼(_low_conf) 재사용,
    추세 표시는 _trend_display(렌더 전용). 정렬·데이터키·임계값 불변."""
    table = []
    for r in rows:
        t = trends.get(r.canonical)
        lc = car_cli._low_conf(r, t) if t is not None else True
        table.append(
            {
                "정규명": r.canonical,
                "부품유형": r.part_type,
                "합산검색량": r.volume,
                "멤버": r.members,
                "추세": _trend_display(t, lc),
                "신뢰도": "저신뢰" if lc else "정상",
                "세대미상": "예" if r.ambiguous else "",
            }
        )
    return table


def render_car_demand() -> None:
    _inject_css()
    st.title("차종 수요 — 모델별 규모 + 추세")
    st.caption(
        "부품 시드 수확 → 차종 인식 → 모델별 합산 규모(C-3) + 데이터랩 월별 추세(C-4). "
        "규모·추세 별도 컬럼 — 단일 매력도 점수 없음."
    )

    col_btn, col_note = st.columns([1, 4])
    with col_btn:
        if st.button("🔄 새로고침", key="car_refresh",
                     help="캐시를 비우고 검색광고+데이터랩을 다시 호출합니다."):
            _load_car_demand.clear()
            st.session_state.pop(_DEMAND_RESULTS_KEY, None)
            st.rerun()
    with col_note:
        st.caption(
            f"결과 세션 유지 · 컷 MODEL_MIN_VOLUME={config.MODEL_MIN_VOLUME:,} · "
            f"추세 {config.TREND_RECENT_MONTHS}개월÷{config.TREND_BASELINE_MONTHS}개월."
        )

    with st.expander("표 해석 주의 · 한계", expanded=False):
        st.markdown(_LIMITS_MD)

    _cached = st.session_state.get(_DEMAND_RESULTS_KEY)
    if _cached is not None:
        rows, trends, note, members_map = _cached
    else:
        try:
            rows, trends, note, members_map = _load_car_demand()
        except Exception as e:  # noqa: BLE001 — 원인 명시(키 미설정/API 실패 등, 조용한 폴백 금지)
            st.error(f"차종 수요 수집 실패: {type(e).__name__}: {e}")
            return
        st.session_state[_DEMAND_RESULTS_KEY] = (rows, trends, note, members_map)

    if note:
        st.warning(note)
    if not rows:
        st.info("표시할 모델이 없습니다(수확 결과가 컷 미만이거나 비었음).")
        return

    # 요약 칩
    n_up = sum(
        1 for r in rows
        if trends.get(r.canonical) is not None and trends[r.canonical].direction == "↑"
    )
    n_new = sum(
        1 for r in rows
        if trends.get(r.canonical) is not None and trends[r.canonical].new_candidate
    )
    _chips([("총 모델", str(len(rows))), ("추세 ↑", str(n_up)), ("신규 후보", str(n_new))])

    # 핵심 강조 카드: 추세 상승 + 신규 후보 (검색량 내림차순)
    hl_rows = sorted(
        [r for r in rows
         if trends.get(r.canonical) is not None
         and (trends[r.canonical].new_candidate or trends[r.canonical].direction == "↑")],
        key=lambda r: r.volume, reverse=True,
    )
    _highlight_table(
        "📈 추세 상승 · 신규 후보 차종",
        ["차종", "검색량", "추세"],
        [
            [r.canonical, f"{r.volume:,}",
             "신규 후보" if trends[r.canonical].new_candidate else "↑"]
            for r in hl_rows[:8]
        ],
        align=["left", "right", "center"],
    )

    # 뷰 선택 — 터미널의 세 뷰와 동일.
    view = st.radio(
        "뷰",
        ["규모순 본표", "추세순", "떠오르는 신차 후보"],
        horizontal=True,
        help="규모순=합산검색량 내림차순 · 추세순=상승 신호 우선 · 신차 후보=추세 ↑ 또는 신규 후보만.",
    )

    if view == "추세순":
        shown = car_cli._sort_rows(rows, trends, "trend")
    elif view == "떠오르는 신차 후보":
        rising = [
            r for r in rows
            if (t := trends.get(r.canonical)) and (t.new_candidate or t.direction == "↑")
        ]
        shown = car_cli._sort_rows(rising, trends, "trend")
    else:
        shown = car_cli._sort_rows(rows, trends, "scale")

    st.caption(f"{view} · {len(shown)}행 (열 머리글 클릭 시 정렬)")
    st.dataframe(
        _demand_table_rows(shown, trends),
        use_container_width=True,
        hide_index=True,
        column_config=_DEMAND_COLUMN_CONFIG,
    )

    # ── 묶인 키워드 펼쳐보기 (검산용) ────────────────────────────────────────
    # 합산검색량이 어떤 연관 키워드로 묶였는지 '원본 그대로' 보여준다(미리 거르지 않음).
    # 목적: 차종 무관 키워드 혼입(오염)을 시경이 눈으로 잡는 것. 새 수집 없음(캐시 안 값).
    st.divider()
    st.subheader("🔍 묶인 키워드 보기 (검산용)")
    st.caption(
        "본표의 합산검색량이 어떤 연관 키워드들로 묶였는지 원본 그대로입니다 — "
        "차종과 무관한 키워드가 섞였는지 직접 확인하세요(미리 거르지 않습니다)."
    )
    combos = {f"{r.canonical} · {r.part_type}": (r.canonical, r.part_type) for r in shown}
    if not combos:
        st.info("현재 뷰에 표시된 행이 없습니다.")
    else:
        pick = st.selectbox("차종 · 부품유형 조합 선택", list(combos.keys()))
        canon, ptype = combos[pick]
        members = sorted(members_map.get((canon, ptype), []), key=lambda kv: kv[1], reverse=True)
        st.dataframe(
            # 멤버 vol==0 은 원래 '< 10'(극소) → 표시만 '<10'. 내부 합산값은 건드리지 않음.
            [{"키워드": rel, "검색량(PC+모바일)": _fmt_vol_display(vol, vol == 0)}
             for rel, vol in members],
            use_container_width=True,
            hide_index=True,
            column_config=_MEMBER_COLUMN_CONFIG,
        )
        member_sum = sum(vol for _, vol in members)
        table_val = next(
            (r.volume for r in shown if (r.canonical, r.part_type) == (canon, ptype)), None
        )
        match = table_val is not None and member_sum == table_val
        st.caption(
            f"멤버 합계 {member_sum:,} {'=' if match else '≠'} 본표 값 "
            f"{table_val:,} (검산)" + ("" if match else "  ⚠️ 불일치")
        )


# ═══════════════════════════════════════════════════════════════════════════
# 화면 2 — 계절 제품 (계절성 + 단일 키워드 규모). 차종 수요와 동일 패턴.
# ═══════════════════════════════════════════════════════════════════════════
@st.cache_data(ttl=3600, show_spinner="계절 제품 수집 중 (데이터랩 + 검색광고)...")
def _load_seasonal():
    """계절 파이프라인(scripts/seasonal_calendar) 재사용 → (rows, winter, 안내문).

    rows: 키워드별 계절성 지표 + 단일 키워드 규모(vol_rec={"volume","low"}).
    winter: 겨울 케어 묶음 '합산 척도' 참고값(본표와 별도). 표 포맷은 호출부가 한다."""
    cid = os.environ.get("NAVER_CLIENT_ID", "").strip()
    csec = os.environ.get("NAVER_CLIENT_SECRET", "").strip()
    if not (cid and csec):
        return [], [], ("데이터랩 키(NAVER_CLIENT_ID/SECRET)가 .env 에 없어 계절성을 "
                        "수집하지 못했습니다.")
    flat = [(kw, season) for season, kws in sc.SEASON_KEYWORDS.items() for kw in kws]
    keywords = [kw for kw, _ in flat]
    season_of = {kw: s for kw, s in flat}

    shape = sc.fetch_shape(sc.SEASON_KEYWORDS, cid, csec)   # 데이터랩(계절 모양)
    vols = sc.fetch_single_volumes(keywords)                # 검색광고 단일 키워드(NAVER_AD_* 검증)

    rows = []
    for kw in keywords:
        a = sc.analyze_shape(shape.get(kw, {}))
        if a is None:
            continue
        rows.append({
            "keyword": kw, "season": season_of[kw], "index": a["index"], "label": a["label"],
            "peak_month": a["peak_month"], "rising_month": a["rising_month"],
            "volume": vols.get(kw, {"volume": None, "low": False}), "stability": a["stability"],
        })

    # 겨울 케어 묶음(합산 척도 — 본표와 절대 섞지 않음). 코어 합산을 겨울 시드에만 적용.
    from src.core.search_volume import fetch_aggregated_volume

    agg = fetch_aggregated_volume(sc.WINTER_CARE_BUNDLE)
    winter = [{"seed": s, "total": int(agg.get(s, {}).get("total_volume", 0)),
               "members": len(agg.get(s, {}).get("member_keywords", []))}
              for s in sc.WINTER_CARE_BUNDLE]
    return rows, winter, None


_SEASON_LIMITS_MD = (
    "**[한계] — 표 해석 주의**\n\n"
    "· 계절성지수 = 최대월 ÷ 연평균(비율). 2.0 이상이면 진짜 계절상품, 1.8~2.0 약한 계절성, "
    "그 미만 상시.\n\n"
    "· 규모 = '단일 키워드' 월검색량(PC+모바일). ★차종 수요는 '여러 연관어 합산' 척도라 "
    "**척도가 다릅니다** — 두 화면의 검색량 숫자를 직접 비교하지 마세요.\n\n"
    f"· 발주 데드라인 = 상승월 1일 − 리드타임 {config.LEAD_TIME_TOTAL_DAYS}일"
    f"(중국 {config.LEAD_TIME_CHINA_DAYS} + 국내 {config.LEAD_TIME_KOREA_DAYS} + 버퍼 "
    f"{config.LEAD_TIME_BUFFER_DAYS}) 역산. 상승월은 데이터랩 3년 곡선에서 **자동 추출**"
    "(고정 라벨 아님)이라 과거 패턴 기준 추정입니다. '🔴 늦음'은 올해 마감이 지난 것 — "
    "표시 데드라인은 내년 기준입니다.\n\n"
    f"· 추천 필터 = 규모 ≥ {config.MARKET_SIZE_THRESHOLD:,} AND 계절성 ≥ {sc.FILTER_MIN_INDEX} "
    "— **발주 상태와 독립**입니다. 전 계절상품의 발주 상태를 보려면 필터를 끄세요(상태는 "
    "필터와 무관하게 행마다 계산되며, 늦음도 숨기지 않습니다)."
)

_SEASON_COLUMN_CONFIG = {
    "제품": st.column_config.TextColumn("제품"),
    "시즌": st.column_config.TextColumn("시즌"),
    "계절성지수": st.column_config.NumberColumn(
        "계절성지수",
        format="%.2f",
        help="최대월 검색량 ÷ 연평균. 2.0 이상이면 진짜 계절상품(특정 시즌에 수요 집중), "
             "1.8~2.0 약한 계절성, 그 미만 상시상품입니다.",
    ),
    "정점/상승월": st.column_config.TextColumn(
        "정점/상승월",
        help="정점월=검색량이 가장 높은 달, 상승월=수요가 오르기 시작하는 달입니다. "
             "발주는 상승월 전에 준비해야 합니다.",
    ),
    "월 검색량(단일 키워드)": st.column_config.TextColumn(
        "월 검색량(단일 키워드)",
        help="이 키워드 하나의 PC+모바일 월간 검색량입니다. ★차종 수요(여러 연관어 합산)와 "
             "달리 단일 키워드 값이라 척도가 다릅니다 — 두 화면 숫자를 직접 비교하지 마세요. "
             "'<10'은 검색광고 API가 '< 10'으로만 주는 극소 검색량(정확값 비공개)입니다.",
    ),
    "안정성": st.column_config.TextColumn(
        "안정성",
        help="연도별 정점월이 ±1개월 안에 반복되면 '안정'(매년 같은 시즌), 더 흔들리면 '불안정'입니다.",
    ),
    "발주 데드라인": st.column_config.TextColumn(
        "발주 데드라인",
        help="가장 가까운 미래 발주 마감일입니다. 상승월(수요가 오르기 시작하는 달) 1일에서 "
             f"리드타임 {config.LEAD_TIME_TOTAL_DAYS}일(중국 {config.LEAD_TIME_CHINA_DAYS}+국내 "
             f"{config.LEAD_TIME_KOREA_DAYS}+버퍼 {config.LEAD_TIME_BUFFER_DAYS})을 역산합니다. "
             "상승월은 데이터랩 3년 곡선에서 자동 추출됩니다.",
    ),
    "발주 상태": st.column_config.TextColumn(
        "발주 상태",
        help="오늘(KST) 기준. 🟢 지금 발주 적기(마감 0~30일) · 🟡 임박(30~60일) · "
             "⚪ 아직 이름(60일 초과) · 🔴 이미 늦음(올해 마감 지남 → 표시 데드라인은 내년 기준). "
             "추천 필터와 무관하게 행마다 계산되며 늦음도 숨기지 않습니다.",
    ),
}

_WINTER_COLUMN_CONFIG = {
    "겨울 시드": st.column_config.TextColumn("겨울 시드"),
    "기기군 합산": st.column_config.NumberColumn("기기군 합산", format="localized"),
    "멤버수": st.column_config.NumberColumn("멤버수", format="localized"),
}


def _season_row(r: dict, deadline: date, status: str) -> dict:
    """계절 rows → st.dataframe dict. 규모는 sc._fmt_volume 로 '<10'/'확인필요'/콤마 표시.
    발주 데드라인/상태는 호출부에서 오늘 기준으로 계산해 넘긴다(캐시와 무관, 매 렌더 갱신)."""
    return {
        "제품": r["keyword"],
        "시즌": r["season"],
        "계절성지수": round(r["index"], 2),
        "정점/상승월": f"{r['peak_month']}월/{r['rising_month']}월",
        "월 검색량(단일 키워드)": sc._fmt_volume(r["volume"]),
        "안정성": r["stability"],
        "발주 데드라인": f"{deadline.isoformat()}까지",
        "발주 상태": status,
    }


def render_seasonal() -> None:
    _inject_css()
    st.title("계절 제품 — 계절성 + 규모(단일 키워드)")
    st.caption(
        "데이터랩 계절성(모양) + 검색광고 단일 키워드 규모. 규모·계절성 별도 컬럼 — "
        "단일 매력도 점수 없음."
    )

    # 화면 전용 사이드바 입력(정렬·필터).
    st.sidebar.markdown("**정렬 · 필터**")
    sort_label = st.sidebar.radio(
        "정렬", ["규모순", "계절성순", "발주 임박순"], key="season_sort",
        help="발주 임박순 = 발주 마감일 가까운 순(이미 늦은 상품은 내년 마감이라 뒤로 갑니다).",
    )
    apply_filter = st.sidebar.toggle(
        f"추천 필터 (규모≥{config.MARKET_SIZE_THRESHOLD:,} AND 계절성≥{sc.FILTER_MIN_INDEX})",
        value=True, key="season_filter", help="끄면 전체 제품을 표시합니다.",
    )

    col_btn, col_note = st.columns([1, 4])
    with col_btn:
        if st.button("🔄 새로고침", key="season_refresh",
                     help="캐시를 비우고 데이터랩+검색광고를 다시 호출합니다."):
            _load_seasonal.clear()
            st.session_state.pop(_SEASONAL_RESULTS_KEY, None)
            st.rerun()
    with col_note:
        st.caption("결과 세션 유지.")

    with st.expander("표 해석 주의 · 계절성 정의 · 한계", expanded=False):
        st.markdown(_SEASON_LIMITS_MD)

    _cached = st.session_state.get(_SEASONAL_RESULTS_KEY)
    if _cached is not None:
        rows, winter, note = _cached
    else:
        try:
            rows, winter, note = _load_seasonal()
        except Exception as e:  # noqa: BLE001 — 원인 명시(키 미설정/API 실패 등, 조용한 폴백 금지)
            st.error(f"계절 제품 수집 실패: {type(e).__name__}: {e}")
            return
        st.session_state[_SEASONAL_RESULTS_KEY] = (rows, winter, note)

    if note:
        st.warning(note)
    if not rows:
        st.info("표시할 계절 제품이 없습니다(계절 시계열이 모두 비어 있음).")
        return

    def passes(r: dict) -> bool:  # 추천 필터 = 규모 AND 계절성 (임계는 config/seasonal 그대로).
        return (sc._vol_num(r["volume"]) >= config.MARKET_SIZE_THRESHOLD
                and r["index"] >= sc.FILTER_MIN_INDEX)

    # 발주 데드라인/상태는 추천 필터와 독립 — 모든 행에 대해 오늘 기준으로 계산(늦음도 숨기지 않음).
    today = _kst_today()
    status_of = {id(r): order_deadline_status(r["rising_month"], today) for r in rows}

    # 요약 칩
    n_recommend = sum(1 for r in rows if passes(r))
    n_prime = sum(1 for r in rows if status_of[id(r)][1] == "🟢 지금 발주 적기")
    n_seasonal = sum(1 for r in rows if r["index"] >= 1.8)
    _chips([
        ("추천 후보", str(n_recommend)),
        ("발주 적기 🟢", str(n_prime)),
        ("계절성 있음", str(n_seasonal)),
    ])

    # 핵심 강조 카드: 발주 적기 제품 (마감 임박순)
    prime_rows = sorted(
        [r for r in rows if status_of[id(r)][1] == "🟢 지금 발주 적기"],
        key=lambda r: status_of[id(r)][0],
    )
    _highlight_table(
        "🟢 지금 발주 적기 제품",
        ["제품", "마감일"],
        [[r["keyword"], status_of[id(r)][0].isoformat() + "까지"]
         for r in prime_rows[:8]],
        align=["left", "center"],
    )

    kept = [r for r in rows if (not apply_filter) or passes(r)]
    if sort_label == "발주 임박순":            # 마감일 가까운 순(늦음=내년 마감이라 뒤로).
        shown = sorted(kept, key=lambda r: status_of[id(r)][0])
    else:
        sort_by = "seasonality" if sort_label == "계절성순" else "scale"
        shown = sc._sorted(kept, sort_by)      # 파이프라인 정렬 헬퍼 재사용

    st.caption(
        f"{sort_label} · {'추천필터 ON' if apply_filter else '전체'} · "
        f"{len(shown)}/{len(rows)}개 · 오늘(KST) {today.isoformat()} · "
        f"리드타임 {config.LEAD_TIME_TOTAL_DAYS}일 역산 (열 머리글 클릭 시 정렬)"
    )
    st.dataframe(
        [_season_row(r, *status_of[id(r)]) for r in shown],
        use_container_width=True,
        hide_index=True,
        column_config=_SEASON_COLUMN_CONFIG,
    )

    # ── 겨울 케어 묶음 (참고 · 합산 척도, 본표와 별도) ──────────────────────────
    st.divider()
    st.subheader("❄️ 겨울 케어 묶음 (참고 · 합산 척도 — 본표와 별도)")
    st.caption(
        "부동액·냉각수·성에·체인을 '합산' 척도로 묶은 참고값입니다. "
        "★본표(단일 키워드 척도) 숫자와 합치거나 비교하지 마세요 — 척도가 다릅니다."
    )
    if winter:
        st.dataframe(
            [{"겨울 시드": w["seed"], "기기군 합산": w["total"], "멤버수": w["members"]}
             for w in winter],
            use_container_width=True,
            hide_index=True,
            column_config=_WINTER_COLUMN_CONFIG,
        )
        st.caption(f"겨울 합산 합계 {sum(w['total'] for w in winter):,} (합산 척도 — 참고용)")


# ═══════════════════════════════════════════════════════════════════════════
# 화면 3 — 키워드 탐색기 (사실만. 8신호·4분면 판정 없음).
# ═══════════════════════════════════════════════════════════════════════════
@st.cache_data(ttl=3600, show_spinner="키워드 수확 중 (검색광고)...")
def _fetch_keyword_explorer(seeds: tuple[str, ...]):
    """시드별 연관어 수확(검색광고 키워드도구) → {rel: (vol, low, compIdx)}.

    코어 함수(dedupe_relkeywords/member_volume) 재사용. 시드 간 dedup(첫 출현 우선).
    판정/필터 없음 — 사실(검색량·경쟁도)만. low=원래 '< 10'(표시용)."""
    adapter = NaverAdapter(list(seeds))  # NAVER_AD_* 키 검증
    out: dict[str, tuple] = {}
    for i, seed in enumerate(seeds):
        if i > 0:
            adapter._sleep(adapter.rate_limit_seconds)  # 호출 간 rate limit
        uniq = dedupe_relkeywords(adapter._request_keywordstool([seed]))
        for rel, row in uniq.items():
            if rel in out:
                continue
            raw_pc, raw_mo = row.get(FIELD_MONTHLY_PC), row.get(FIELD_MONTHLY_MOBILE)
            vol = member_volume(row)  # '< 10' → 0 (코어 보수 처리, 계산 불변)
            low = vol == 0 and any(str(v).strip().startswith("<") for v in (raw_pc, raw_mo))
            out[rel] = (vol, low, row.get(FIELD_COMP_IDX))
    return out


_EXPLORER_COLUMN_CONFIG = {
    "키워드": st.column_config.TextColumn("키워드"),
    "검색량(PC+모바일)": st.column_config.TextColumn(
        "검색량(PC+모바일)",
        help="해당 키워드의 PC+모바일 월간 검색량입니다. '<10'은 검색광고 API가 '< 10'으로만 "
             "주는 극소 검색량(정확값 비공개)입니다.",
    ),
    "경쟁도": st.column_config.TextColumn(
        "경쟁도",
        help="검색광고 API가 주는 경쟁정도(낮음/중간/높음) 값 그대로입니다.",
    ),
}


def render_keyword_explorer() -> None:
    _inject_css()
    st.title("키워드 탐색기")
    st.caption(
        "시드 → 연관 키워드 수확 → 검색량·경쟁도 확인. "
        "판정 없음 — 사실만 보여줍니다."
    )

    st.sidebar.markdown("**시드 입력**")
    seed_text = st.sidebar.text_area(
        "시드 키워드(줄바꿈 또는 쉼표로 구분)",
        key="explorer_seeds",
        help="예: 로봇청소기, 공기청정기, 전동칫솔. 각 시드의 연관키워드를 검색광고 키워드도구로 수확합니다.",
    )
    seeds = [s.strip() for s in seed_text.replace(",", "\n").splitlines() if s.strip()]

    col_btn, col_note = st.columns([1, 4])
    with col_btn:
        if st.button("🔄 새로고침", key="explorer_refresh",
                     help="캐시를 비우고 검색광고를 다시 호출합니다."):
            _fetch_keyword_explorer.clear()
            st.rerun()
    with col_note:
        st.caption("검색광고 키워드도구(NAVER_AD_*) · 1시간 캐시.")

    if not seeds:
        st.info("사이드바에 시드 키워드를 입력하세요.")
        return

    try:
        data = _fetch_keyword_explorer(tuple(seeds))
    except Exception as e:  # noqa: BLE001 — 원인 명시(키 미설정/API 실패 등, 조용한 폴백 금지)
        st.error(f"키워드 수확 실패: {type(e).__name__}: {e}")
        return

    if not data:
        st.info("연관 키워드를 찾지 못했습니다.")
        return

    items = sorted(data.items(), key=lambda kv: kv[1][0], reverse=True)  # 검색량 내림차순

    # 요약 칩
    n_low = sum(1 for _, (_, low, _) in items if low)
    n_high = sum(1 for _, (_, _, comp) in items if comp == "높음")
    _chips([
        ("총 키워드", str(len(items))),
        ("검색량 <10", str(n_low)),
        ("경쟁 높음", str(n_high)),
    ])

    table = [
        {"키워드": rel, "검색량(PC+모바일)": _fmt_vol_display(vol, low), "경쟁도": comp or "-"}
        for rel, (vol, low, comp) in items
    ]
    st.caption(f"시드 {len(seeds)}개 → 연관 키워드 {len(table)}개 (검색량 내림차순)")
    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        column_config=_EXPLORER_COLUMN_CONFIG,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 화면 4 — 체험단 타겟 선정 (차종×제품 검색량 + 블로그 문서수 + 비율 분류)
# ═══════════════════════════════════════════════════════════════════════════

_TEAMP_LIMITS_MD = (
    "**[주의] 블로그 문서수는 근사치이며 블로그 노출 난이도(블로그 지수)와 다릅니다.**\n\n"
    "네이버 블로그 검색 API의 `total`은 네이버 내부 추정값으로 실제 노출 경쟁과는 차이가 있습니다. "
    "비율이 낮은 후보가 우선순위 신호이지, 노출 보증이 아닙니다. "
    "**후보 차종은 반드시 직접 검색해 경쟁 글의 품질·블로그 지수를 확인하세요.**"
)

_TEAMP_COLUMN_CONFIG = {
    "키워드": st.column_config.TextColumn(
        "키워드",
        width="large",
        help="검색광고 키워드도구 연관 키워드 원문. 이 문자열 그대로 검색량과 문서수를 잽니다.",
    ),
    "검색량": st.column_config.NumberColumn(
        "검색량",
        width="small",
        format="localized",
        help="이 키워드의 PC+모바일 월간 검색량(검색광고 키워드도구 값).",
    ),
    "문서수": st.column_config.TextColumn(
        "문서수",
        width="small",
        help="네이버 블로그 검색 '{키워드 원문}' 결과의 total 값입니다. "
             "근사치이며 실제 노출 경쟁(블로그 지수)을 반영하지 않습니다. "
             "'–'는 429 재시도 소진으로 조회 실패한 항목입니다.",
    ),
    "비율": st.column_config.TextColumn(
        "비율",
        width="small",
        help=(
            "문서수 ÷ 검색량 (동일 키워드 기준). "
            "1 미만 = 블로그 글이 수요보다 적어 노릴 자리(황금), "
            "3 초과 = 포화. "
            "단 문서수는 근사치이고 상위 글의 블로그 지수는 반영 못 하니, "
            "후보 키워드는 직접 검색해 경쟁 글 품질을 확인하세요."
        ),
    ),
    "최근3개월": st.column_config.TextColumn(
        "최근3개월",
        width="medium",
        help=(
            "최근 3개월 내 작성 블로그 글 수(추정, 최신순 상위 100개 기준). "
            "낮을수록 최근 경쟁 약함. "
            "🔴100+ 매우 치열 / 🟡30~99 꽤 있음 / 🟢6~29 약함 / 🟢0~5 거의 방치. "
            "직접 검색으로 확인 권장."
        ),
    ),
    "최근비중": st.column_config.TextColumn(
        "최근비중",
        width="small",
        help=(
            "전체 블로그 글 중 최근 3개월 글의 비중(%). "
            "낮을수록 과거엔 많았으나 지금은 식어 새 글이 들어갈 틈. "
            "높을수록 지금 달아오르는 중. "
            "검색량 크고 + 비중 낮으면 노려볼 만. "
            "최근 글이 100+(상한)인 키워드는 계산 불가('—')."
        ),
    ),
    "분류": st.column_config.TextColumn(
        "분류",
        width="medium",
        help="🟡 황금: 비율 < 1.0 / 🟢 해볼만: 1.0 ≤ 비율 ≤ 3.0 / 🔴 포화/후순위: 비율 > 3.0",
    ),
    "차종": st.column_config.TextColumn(
        "차종",
        width="small",
        help="차종 인식 사전에서 인식된 차종명(표시용). 미인식 키워드는 빈칸.",
    ),
}

_TEAMP_TOP10_COLUMN_CONFIG = {
    "키워드": st.column_config.TextColumn("키워드"),
    "차종": st.column_config.TextColumn("차종"),
    "검색량": st.column_config.NumberColumn("검색량", format="localized"),
    "비율": st.column_config.NumberColumn("비율", format="%.2f"),
}


@st.cache_data(ttl=3600, show_spinner="키워드 수확 중 (검색광고)...")
def _harvest_teamp_kw(products: tuple[str, ...]) -> list[tuple[str, str, int]]:
    """제품 키워드 → 연관 키워드 수확 → (keyword, car_model, volume) 리스트.

    반환: [(keyword, car_model_display, volume), ...] 검색량 내림차순.
    volume=0(원래 '<10') 키워드는 harvest 단계에서 제외.
    차종 인식(car_model)은 표시 전용 — 필터·그룹핑 미사용.
    """
    idx = load_car_models()
    adapter = NaverAdapter(list(products))  # NAVER_AD_* 키 검증
    return harvest_teamp_kw_items(adapter, list(products), idx)


def render_teamp() -> None:
    _inject_css()

    # 위젯 렌더 전: 캐시 조회 + 탭 복귀 시 text_area 복원 (위젯 evict 대응)
    _cached = st.session_state.get(_TEAMP_RESULTS_KEY)
    _last_kws = st.session_state.get("_teamp_last_keywords")
    if _last_kws and "teamp_products" not in st.session_state:
        st.session_state["teamp_products"] = ", ".join(_last_kws)

    st.title("체험단 타겟 선정")
    st.caption(
        "제품 키워드 → 연관 키워드 수확 → 키워드별 검색량 × 블로그 문서수 → 비율 분류. "
        "비율 낮은 키워드가 체험단 공략 후보입니다."
    )

    # 사이드바: 제품 키워드 입력 + 정렬
    st.sidebar.markdown("**제품 키워드**")
    product_text = st.sidebar.text_area(
        "쉼표 또는 줄바꿈 구분",
        key="teamp_products",
        help="예: 에어컨필터, 자동차에어컨필터. 각 키워드를 시드로 연관 키워드를 수확해 경쟁도를 분석합니다.",
    )
    st.sidebar.divider()
    st.sidebar.markdown("**정렬**")
    sort_label = st.sidebar.radio(
        "본표",
        ["비율 오름차순 (황금 위)", "검색량 내림차순", "검색량↑ + 최근글↓ (숨은 기회)"],
        key="teamp_sort",
        help=(
            "기본: 비율 오름차순(황금 후보가 위). "
            "검색량순: 규모 큰 키워드 먼저. "
            "검색량↑ + 최근글↓: 검색량이 크면서 최근 3개월 글이 적은 키워드 — "
            "총 문서수는 많아도 최신 경쟁이 약한 숨은 기회 탐색."
        ),
    )
    top10_sort_label = st.sidebar.radio(
        "황금 TOP10",
        ["비율 낮은 순", "검색량 높은 순"],
        key="teamp_top10_sort",
        help="기본: 비율 낮은 순(공략 여지 큰 순). 검색량 높은 순으로 바꾸면 규모 큰 황금 후보를 먼저 볼 수 있습니다.",
    )

    products = [s.strip() for s in product_text.replace(",", "\n").splitlines() if s.strip()]

    # 마지막 키워드 보존 (비위젯 키 → 탭 전환 후에도 유지)
    if products:
        st.session_state["_teamp_last_keywords"] = products

    col_btn, col_note = st.columns([1, 4])
    with col_btn:
        if st.button("🔄 새로고침", key="teamp_refresh",
                     help="캐시를 비우고 검색광고+블로그 API를 다시 호출합니다."):
            _harvest_teamp_kw.clear()
            st.session_state.pop(_TEAMP_RESULTS_KEY, None)
            for k in [k for k in st.session_state if k.startswith("_teamp_blog_")]:
                del st.session_state[k]
            st.rerun()
    with col_note:
        st.caption(
            f"결과 세션 유지 · 탭 전환 재추출 없음 · "
            f"황금 < {config.TEAMP_RATIO_GOLD} / 해볼만 ≤ {config.TEAMP_RATIO_OK} / 초과 = 포화."
        )

    with st.expander("블로그 문서수 주의 · 한계", expanded=False):
        st.markdown(_TEAMP_LIMITS_MD)

    # products=[] 이면서 캐시가 살아있으면 → 탭 복귀 케이스, 캐시에서 복원
    if not products:
        if _cached is not None:
            products = _cached["products"]
            rows = _cached["rows"]
            failed_items = _cached["failed_items"]
        else:
            st.info("사이드바에 제품 키워드를 입력하세요. 예: 에어컨필터, 자동차에어컨필터")
            return
    elif _cached is not None and _cached["products"] == products:
        rows = _cached["rows"]
        failed_items = _cached["failed_items"]
    else:
        # 새로 수집 — 키워드 변경 또는 새로고침 시
        cid = os.environ.get("NAVER_CLIENT_ID", "").strip()
        csec = os.environ.get("NAVER_CLIENT_SECRET", "").strip()
        if not (cid and csec):
            st.error(
                "NAVER_CLIENT_ID/SECRET이 .env에 없어 블로그 문서수를 가져올 수 없습니다. "
                "검색광고 API 키(NAVER_AD_*)와 별도로 데이터랩·블로그용 키를 .env에 추가하세요."
            )
            return

        try:
            kw_items = _harvest_teamp_kw(tuple(products))
        except Exception as e:  # noqa: BLE001
            st.error(f"키워드 수확 실패: {type(e).__name__}: {e}")
            return

        if not kw_items:
            st.info("표시할 데이터가 없습니다(제품명 포함 키워드가 없거나 검색량 <10만 있음).")
            return

        total = len(kw_items)
        prog = st.progress(0, f"블로그 문서수 조회 중... 0/{total}")

        def _prog_cb(done: int, tot: int) -> None:
            prog.progress(done / tot, f"블로그 문서수 조회 중... {done}/{tot}")

        rows, failed_items = fetch_teamp_kw_rows_partial(
            kw_items,
            lambda q: fetch_blog_count(q, cid, csec),
            max_workers=config.NAVER_BLOG_MAX_WORKERS,
            on_progress=_prog_cb,
        )
        prog.empty()

        # 최신성 조회: 황금+해볼만 전체 + 포화 중 검색량 큰 것
        recent_targets = [
            r for r in rows
            if r.grade != "🔴 포화/후순위" or r.volume >= config.TEAMP_SATURATED_MIN_VOLUME
        ]
        if recent_targets:
            prog_r = st.progress(0, f"최근 {config.TEAMP_RECENT_MONTHS}개월 글 수 조회 중... 0/{len(recent_targets)}")

            def _prog_r_cb(done: int, tot: int) -> None:
                prog_r.progress(done / tot, f"최근 {config.TEAMP_RECENT_MONTHS}개월 글 수 조회 중... {done}/{tot}")

            rows = fetch_recent_3m_docs_partial(
                rows,
                lambda q: fetch_recent_blog_count(q, cid, csec),
                max_workers=config.NAVER_BLOG_MAX_WORKERS,
                on_progress=_prog_r_cb,
            )
            prog_r.empty()

        st.session_state[_TEAMP_RESULTS_KEY] = {
            "products": products,
            "rows": rows,
            "failed_items": failed_items,
        }

    if not rows and not failed_items:
        st.info("표시할 데이터가 없습니다.")
        return

    # ── 요약 칩 + 황금 TOP10 카드 ────────────────────────────────────────────
    if top10_sort_label == "비율 낮은 순":
        top10 = top_gold_kw_rows_by_ratio(rows, n=10)
    else:
        top10 = top_gold_kw_rows(rows, n=10)

    grade_counts = {"🟡 황금": 0, "🟢 해볼만": 0, "🔴 포화/후순위": 0}
    for r in rows:
        grade_counts[r.grade] = grade_counts.get(r.grade, 0) + 1

    recent_queried = sum(1 for r in rows if r.recent_3m_docs is not None)
    chip_items = [
        ("🟡 황금", str(grade_counts["🟡 황금"])),
        ("🟢 해볼만", str(grade_counts["🟢 해볼만"])),
        ("🔴 포화/후순위", str(grade_counts["🔴 포화/후순위"])),
    ]
    if recent_queried:
        chip_items.append(("📅 최신성 조회", str(recent_queried)))
    if failed_items:
        chip_items.append(("⚠️ 조회실패", str(len(failed_items))))
    _chips(chip_items)

    if top10:
        # 비율 낮은 순 → 동률은 검색량 높은 순 (표시 정렬만, 10개 선택은 top_gold_kw_rows* 결과 그대로)
        display_top10 = sorted(top10, key=lambda r: (r.ratio, -r.volume))
        _highlight_table(
            f"🟡 황금 TOP {len(display_top10)} — {top10_sort_label}",
            ["순위", "키워드", "검색량", "비율"],
            [[str(i + 1), r.keyword, f"{r.volume:,}", f"{r.ratio:.2f}"]
             for i, r in enumerate(display_top10)],
            align=["center", "left", "right", "right"],
        )
    else:
        st.info("황금 분류 후보가 없습니다(전체가 해볼만 이상).")

    # ── 본표 ────────────────────────────────────────────────────────────────
    st.divider()
    st.subheader("본표")
    if sort_label == "검색량 내림차순":
        shown = sorted(rows, key=lambda r: r.volume, reverse=True)
    elif sort_label == "검색량↑ + 최근글↓ (숨은 기회)":
        shown = sorted(
            rows,
            key=lambda r: (
                -r.volume,
                r.recent_3m_docs if r.recent_3m_docs is not None else float("inf"),
            ),
        )
    else:
        shown = sorted(rows, key=lambda r: r.ratio)

    st.caption(
        f"{sort_label} · {len(shown)}행"
        + (f" + 실패 {len(failed_items)}건" if failed_items else "")
        + f" · 제품 키워드: {', '.join(products)} (열 머리글 클릭 시 정렬)"
    )

    # 컬럼 순서: 키워드·검색량·문서수·비율·최근3개월·최근비중·분류 (핵심 7개) | 차종(참고, 우측)
    table_rows = [
        {
            "키워드": r.keyword,
            "검색량": r.volume,
            "문서수": f"{r.doc_count:,}",
            "비율": f"{r.ratio:.2f}",
            "최근3개월": format_recent_3m(r.recent_3m_docs),
            "최근비중": format_recent_ratio(r.recent_3m_docs, r.doc_count),
            "분류": r.grade,
            "차종": r.car_model,
        }
        for r in shown
    ]
    # 조회 실패 항목: 맨 아래, 문서수·비율·최근3개월·최근비중 '–'
    for kw, cm, v in failed_items:
        table_rows.append({
            "키워드": kw,
            "검색량": v,
            "문서수": "–", "비율": "–", "최근3개월": "–", "최근비중": "–",
            "분류": "⚠️ 조회실패",
            "차종": cm,
        })

    st.dataframe(
        table_rows,
        use_container_width=True,
        hide_index=True,
        column_config=_TEAMP_COLUMN_CONFIG,
        column_order=["키워드", "검색량", "문서수", "비율", "최근3개월", "최근비중", "분류", "차종"],
    )

    if failed_items:
        st.warning(
            f"⚠️ {len(failed_items)}건 조회 실패 (429 재시도 소진) — "
            "새로고침으로 재시도하거나, 잠시 후 다시 시도하세요: "
            + ", ".join(kw for kw, _, _ in failed_items)
        )


# ═══════════════════════════════════════════════════════════════════════════
# 사이드바 화면 선택 → 디스패치
# ═══════════════════════════════════════════════════════════════════════════
st.sidebar.markdown("**화면 선택**")
_SCREEN = st.sidebar.radio(
    "화면 선택",
    ["차종 수요", "계절 제품", "키워드 탐색기", "체험단 타겟"],
    label_visibility="collapsed",
)
st.sidebar.divider()

if _SCREEN == "차종 수요":
    render_car_demand()
elif _SCREEN == "계절 제품":
    render_seasonal()
elif _SCREEN == "체험단 타겟":
    render_teamp()
else:
    render_keyword_explorer()
