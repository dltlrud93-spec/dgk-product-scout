"""
app.py — dgk-product-scout 대시보드 (Streamlit).

사이드바 '화면 선택'으로 화면 전환:
  · 차종 수요   — Phase C 차종 수요 스캐너(규모 C-3 + 추세 C-4). 단순 표시(등록 대조 없음).
  · 계절 제품   — 계절성(데이터랩) + 규모(검색광고 단일 키워드) 통합 랭킹.
  · 키워드 탐색기 — 시드 → 연관어 수확 → 키워드·검색량·경쟁도(사실만, 판정 없음).
  · 체험단 타겟  — 차종×제품 검색량 + 블로그 문서수 + 비율 분류.
  · 체험단 양식  — 레뷰 빈 docx 템플릿에 입력값을 채워 양식 생성(미리보기·다운로드).

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

_DOTENV_SNAPSHOT: dict[str, str] = {}
try:
    # .env 의 네이버 API 키를 환경변수로 로드(없어도 무시 — 키 검증은 어댑터가 명시 처리).
    from dotenv import dotenv_values, load_dotenv

    load_dotenv()
    # ★ .env 원본값 스냅샷(권위 출처). st.secrets 접근이 os.environ 을 플레이스홀더로
    #   덮어써도(1단계 함정), 이 스냅샷의 '진짜' 키로 항상 복구하기 위함.
    _DOTENV_SNAPSHOT = {k: v for k, v in dotenv_values().items() if v}
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
    fetch_blog_titles,
    fetch_recent_blog_count,
    fetch_recent_3m_docs_partial,
    fetch_teamp_kw_rows_partial,
    format_recent_3m,
    format_recent_ratio,
    harvest_teamp_kw_items,
    top_gold_kw_rows,
    top_gold_kw_rows_by_ratio,
)
from src.core.jogyeonpyo import (
    harvest_jogyeonpyo_kw_items,
    read_car_models,
)
from src.core.search_volume import (
    FIELD_COMP_IDX,
    FIELD_MONTHLY_MOBILE,
    FIELD_MONTHLY_PC,
    dedupe_relkeywords,
    member_volume,
)
from src.core.secrets_util import resolve_secret
from src.revu_form import (
    SUBTITLE_MAX,
    TEMPLATE_PATH as REVU_TEMPLATE_PATH,
    TITLE_MAX,
    RevuFormData,
    assemble_tracking_url,
    build_revu_docx,
    deserialize_form,
    find_banned_words,
    merge_keywords,
    mission_angles,
    mission_block,
    revu_form_defaults,
    save_filename_json,
    serialize_form,
    suggest_filename,
)
from src.core.keyword_ai import generate_ai_keywords
from src.core.keyword_intent import BADGE, classify_intent
from src.core.keyword_reco import (
    partition_banned,
    recommend_blog_keywords,
    recommend_keywords,
)

st.set_page_config(page_title="dgk-product-scout", layout="wide")


_NAVER_ENV_KEYS = (
    "NAVER_AD_API_KEY",
    "NAVER_AD_SECRET_KEY",
    "NAVER_AD_CUSTOMER_ID",
    "NAVER_CLIENT_ID",
    "NAVER_CLIENT_SECRET",
)


def _secret_candidates(key: str) -> list:
    """네이버 키 1개의 후보값을 '우선순위 순서'로 모은다(진짜값 우선 채택용).

    순서: ① .env 원본 스냅샷(권위) → ② st.secrets 최상위 → ③ st.secrets["auth"] →
          ④ 현재 os.environ. resolve_secret 이 플레이스홀더를 건너뛰고 첫 진짜값을 고른다.
    """
    cands: list = [_DOTENV_SNAPSHOT.get(key)]
    try:
        cands.append(st.secrets[key])
    except (KeyError, AttributeError, FileNotFoundError):
        pass
    try:
        cands.append(st.secrets["auth"][key])
    except (KeyError, AttributeError, FileNotFoundError):
        pass
    cands.append(os.environ.get(key))
    return cands


def _ensure_naver_env() -> None:
    """네이버 키를 '진짜값'으로 os.environ 에 재확정(플레이스홀더 덮어쓰기 방지).

    ★1단계 함정 대응: st.secrets 접근 시 Streamlit 이 로컬 secrets.toml 의 플레이스홀더
      네이버 키를 os.environ 에 export 해 .env 진짜 키를 덮어쓴다. 조견표 읽기가
      st.secrets 를 건드리므로, 네이버 호출 직전 이 함수로 진짜값을 다시 박는다(idempotent).
    조견표 인증(gcp_service_account)은 jogyeonpyo.py 가 st.secrets 에서만 읽어 분리됨 — 충돌 없음.
    """
    for key in _NAVER_ENV_KEYS:
        real = resolve_secret(_secret_candidates(key))
        if real:
            os.environ[key] = real
        elif key in os.environ and not _DOTENV_SNAPSHOT.get(key):
            # 진짜값이 어디에도 없는데 환경엔 플레이스홀더만 있는 경우 → 제거(명확한 키 오류 유도).
            try:
                del os.environ[key]
            except KeyError:
                pass


# 구버전 호출부 호환 별칭.
_load_secrets_to_env = _ensure_naver_env


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
# 게이트가 st.secrets["auth"] 에 접근하며 os.environ 을 플레이스홀더로 오염시킬 수 있어
# 인증 통과 직후 네이버 키를 진짜값으로 재확정한다(모든 화면 공통 보호).
_ensure_naver_env()


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

# 체험단 '키워드 소스' 표시 라벨(표시 전용). 분기는 use_assoc/use_jp 플래그로 — 라벨 변경에 안전.
_SRC_ASSOC = "키워드로 차종 검색"   # 연관어 수확(제품 키워드 시드 → 네이버 연관어)
_SRC_DATA = "데이터로 차종 검색"    # 조견표 차종 × 제품 직접 생성
_SRC_HYBRID = "하이브리드 모드"     # 둘 다(연관어 + 조견표 합쳐 중복 제거)


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
        ["차종", "부품", "검색량", "추세"],
        [
            [r.canonical, r.part_type, f"{r.volume:,}",
             "신규 후보" if trends[r.canonical].new_candidate else "↑"]
            for r in hl_rows[:8]
        ],
        align=["left", "left", "right", "center"],
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


def _jogyeonpyo_sheet_id() -> str | None:
    """조견표 시트 ID — st.secrets['jogyeonpyo_sheet_id'] → env. Public repo 노출 회피로 코드 비박음."""
    try:
        if "jogyeonpyo_sheet_id" in st.secrets:
            return str(st.secrets["jogyeonpyo_sheet_id"])
    except (KeyError, AttributeError, FileNotFoundError):
        pass
    return os.environ.get("JOGYEONPYO_SHEET_ID") or _DOTENV_SNAPSHOT.get("JOGYEONPYO_SHEET_ID")


@st.cache_data(ttl=config.JOGYEONPYO_CACHE_TTL, show_spinner="데이터 차종 읽는 중 (구글시트)...")
def _read_jogyeonpyo_models(worksheet: str, limit: int) -> list[str]:
    """조견표 차종 목록 읽기(캐시). 같은 (worksheet, limit) 재요청 시 시트 재조회 없이 캐시 히트.

    ★캐시 대상은 '시트 읽기'(차종 목록)만 — 검색량/문서수는 매번 진행바와 함께 라이브 조회.
    sheet_id 는 _jogyeonpyo_sheet_id() 가 secrets/env 에서 읽어 cache 키에 영향 없음(동일 시트).
    """
    return read_car_models(
        worksheet=worksheet,
        sheet_id=_jogyeonpyo_sheet_id(),
        limit=limit,
    )


def render_teamp() -> None:
    _inject_css()

    # 위젯 렌더 전: 캐시 조회 + 탭 복귀 시 text_area 복원 (위젯 evict 대응)
    _cached = st.session_state.get(_TEAMP_RESULTS_KEY)
    _last_kws = st.session_state.get("_teamp_last_keywords")
    if _last_kws and "teamp_products" not in st.session_state:
        st.session_state["teamp_products"] = ", ".join(_last_kws)

    st.title("체험단 타겟 선정")
    st.caption(
        f"키워드 소스({_SRC_ASSOC} / {_SRC_DATA} / {_SRC_HYBRID}) → 키워드별 검색량 × 블로그 문서수 → 비율 분류. "
        "비율 낮은 키워드가 체험단 공략 후보입니다."
    )

    # 사이드바: 키워드 소스 선택
    # ★표시 라벨만. 내부 분기는 use_assoc/use_jp 플래그로 — 라벨 문자열 분기 최소화.
    st.sidebar.markdown("**키워드 소스**")
    source_label = st.sidebar.radio(
        "키워드 소스",
        [_SRC_ASSOC, _SRC_DATA, _SRC_HYBRID],
        key="teamp_source",
        label_visibility="collapsed",
        help=(
            f"{_SRC_ASSOC}: 제품 키워드 → 네이버 연관 키워드(현재 방식). "
            f"{_SRC_DATA}: 데이터 차종 × 제품으로 직접 생성(연관어로 못 잡는 신차 발굴). "
            f"{_SRC_HYBRID}: 두 소스를 합쳐 키워드 중복 제거."
        ),
    )
    use_assoc = source_label in (_SRC_ASSOC, _SRC_HYBRID)   # 연관어 시드 사용 모드
    use_jp = source_label in (_SRC_DATA, _SRC_HYBRID)       # 조견표 차종 사용 모드

    # 차종 상한: env(정수) > config(None=전체). 0/빈값 = 전체.
    _env_lim = os.environ.get("JOGYEONPYO_TEST_LIMIT", "").strip()
    jp_limit = int(_env_lim) if _env_lim.isdigit() and int(_env_lim) > 0 else config.JOGYEONPYO_TEST_LIMIT
    jp_product_label = None
    jp_conf = None
    # 조견표 제품(탭) 선택 — 데이터/하이브리드 모드에서만 표시(연관어 단독은 불필요).
    if use_jp:
        jp_product_label = st.sidebar.selectbox(
            "데이터 제품",
            list(config.JOGYEONPYO_PRODUCTS),
            key="teamp_jp_product",
            help="데이터 제품 선택 — 그 제품의 차종 × 이 제품으로 키워드를 만듭니다. "
                 "한 번에 한 제품(탭)만 조회합니다(전 제품 동시 조회 없음).",
        )
        jp_conf = config.JOGYEONPYO_PRODUCTS[jp_product_label]
        if jp_limit:
            st.sidebar.caption(f"데이터 차종 상한 {jp_limit}개 (env JOGYEONPYO_TEST_LIMIT).")
        else:
            st.sidebar.caption(f"데이터 **{jp_product_label} 전체** 조회 (느릴 수 있음 · 6시간 캐시).")

    # 사이드바: 제품 키워드 입력 — 연관어 시드가 필요한 모드에서만 표시.
    # (데이터로 차종 검색=조견표 단독 모드에선 시드 불필요 → 입력란 자체를 숨김)
    if use_assoc:
        st.sidebar.markdown("**제품 키워드**")
        if source_label == _SRC_HYBRID:
            st.sidebar.caption("연관어 수확용 시드 (하이브리드는 연관어도 수확).")
        product_text = st.sidebar.text_area(
            "쉼표 또는 줄바꿈 구분",
            key="teamp_products",
            help="예: 에어컨필터, 자동차에어컨필터. 각 키워드를 시드로 연관 키워드를 수확해 경쟁도를 분석합니다.",
        )
    else:
        product_text = ""
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
                     help="캐시를 비우고 검색광고+블로그+데이터를 다시 호출합니다."):
            _harvest_teamp_kw.clear()
            _read_jogyeonpyo_models.clear()
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

    # 요청 식별 서명 — 소스/제품키워드/조견표제품/상한이 같으면 세션 캐시 재사용(탭 전환 무재추출).
    signature = (
        source_label,
        tuple(products) if use_assoc else (),
        jp_product_label or "",
        jp_limit if use_jp else 0,
    )
    valid_request = (use_assoc and bool(products)) or use_jp
    jp_failed: list[str] = []

    if not valid_request:
        # 입력 없음 — 캐시 있으면 탭 복귀로 보고 복원, 없으면 안내
        if _cached is not None:
            products = _cached.get("products", products)
            rows = _cached["rows"]
            failed_items = _cached["failed_items"]
            jp_failed = _cached.get("jp_failed", [])
        else:
            st.info("사이드바에서 키워드 소스를 고르고, 연관어 모드면 제품 키워드를 입력하세요.")
            return
    elif _cached is not None and _cached.get("signature") == signature:
        rows = _cached["rows"]
        failed_items = _cached["failed_items"]
        jp_failed = _cached.get("jp_failed", [])
    else:
        # 새로 수집 — 소스·키워드·조견표제품 변경 또는 새로고침 시
        # ★조견표 읽기(st.secrets 접근)가 네이버 키를 플레이스홀더로 오염시킬 수 있어
        #   네이버 호출 직전마다 _ensure_naver_env() 로 진짜 키를 재확정한다(1단계 함정 대응).
        _ensure_naver_env()
        cid = os.environ.get("NAVER_CLIENT_ID", "").strip()
        csec = os.environ.get("NAVER_CLIENT_SECRET", "").strip()
        if not (cid and csec):
            st.error(
                "NAVER_CLIENT_ID/SECRET이 .env에 없어 블로그 문서수를 가져올 수 없습니다. "
                "검색광고 API 키(NAVER_AD_*)와 별도로 데이터랩·블로그용 키를 .env에 추가하세요."
            )
            return

        kw_items: list[tuple[str, str, int]] = []

        # ① 연관어 수확
        if use_assoc and products:
            try:
                kw_items += _harvest_teamp_kw(tuple(products))
            except Exception as e:  # noqa: BLE001
                st.error(f"연관어 키워드 수확 실패: {type(e).__name__}: {e}")
                return

        # ② 조견표 차종 × 제품 (검색량 라이브 조회 + 진행바 + 429 실패 집계)
        if use_jp:
            try:
                models = _read_jogyeonpyo_models(jp_conf["worksheet"], jp_limit)
            except Exception as e:  # noqa: BLE001
                st.error(
                    f"데이터 읽기 실패: {type(e).__name__}: {e} — "
                    "st.secrets['gcp_service_account']·['jogyeonpyo_sheet_id'] 또는 "
                    "서비스계정 공유를 확인하세요."
                )
                return
            # 조견표 읽기가 st.secrets 를 건드려 네이버 키 오염 가능 → 즉시 재확정.
            _ensure_naver_env()
            cid = os.environ.get("NAVER_CLIENT_ID", "").strip()
            csec = os.environ.get("NAVER_CLIENT_SECRET", "").strip()
            try:
                jp_adapter = NaverAdapter([jp_conf["product_kw"]])  # NAVER_AD_* 키 검증
            except Exception as e:  # noqa: BLE001
                st.error(f"검색광고 키 오류: {type(e).__name__}: {e}")
                return
            total_m = len(models)
            est_min = total_m * config.JOGYEONPYO_SECONDS_PER_MODEL / 60
            st.info(
                f"📊 데이터 **{jp_product_label}** {total_m}개 차종 조회 — "
                f"약 **{est_min:.1f}분** 소요 예상(차종당 ~{config.JOGYEONPYO_SECONDS_PER_MODEL}초). "
                "진행 중 다른 탭 이동 가능, 결과는 6시간 캐시됩니다. "
                "검색량 0 차종은 블로그 조회 전 자동 제외(시간 단축)."
            )
            prog_v = st.progress(0, f"데이터 검색량 조회 중... 0/{total_m} (차종 {total_m}개)")

            def _vcb(done: int, tot: int) -> None:
                prog_v.progress(done / tot, f"데이터 검색량 조회 중... {done}/{tot} 차종")

            jp_items, jp_failed = harvest_jogyeonpyo_kw_items(
                jp_adapter, models, jp_conf["product_kw"], on_progress=_vcb,
            )
            prog_v.empty()
            kw_items += jp_items

        # ③ 하이브리드: 키워드 기준 중복 제거(연관어·조견표가 같은 키워드 만들면 1개만)
        if use_assoc and use_jp:
            seen: set[str] = set()
            deduped: list[tuple[str, str, int]] = []
            for kw, cm, v in kw_items:
                if kw in seen:
                    continue
                seen.add(kw)
                deduped.append((kw, cm, v))
            kw_items = deduped

        if not kw_items:
            msg = "표시할 데이터가 없습니다(검색량>0 키워드 없음)."
            if jp_failed:
                msg += f" 데이터 검색량 실패 {len(jp_failed)}건(429)."
            st.info(msg)
            st.session_state[_TEAMP_RESULTS_KEY] = {
                "signature": signature, "products": products,
                "rows": [], "failed_items": [], "jp_failed": jp_failed,
            }
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
            "signature": signature,
            "products": products,
            "rows": rows,
            "failed_items": failed_items,
            "jp_failed": jp_failed,
        }

    if not rows and not failed_items:
        if jp_failed:
            st.warning(f"⚠️ 데이터 검색량 {len(jp_failed)}건 조회 실패(429) — 새로고침으로 재시도하세요.")
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
        chip_items.append(("⚠️ 블로그 실패", str(len(failed_items))))
    if jp_failed:
        chip_items.append(("⚠️ 데이터 검색량 실패", str(len(jp_failed))))
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

    _src_desc = f"소스: {source_label}"
    if use_jp and jp_product_label:
        _src_desc += f" · 데이터 제품: {jp_product_label} (앞 {jp_limit}개)"
    if use_assoc and products:
        _src_desc += f" · 제품 키워드: {', '.join(products)}"
    st.caption(
        f"{sort_label} · {len(shown)}행"
        + (f" + 블로그실패 {len(failed_items)}건" if failed_items else "")
        + (f" + 데이터검색량실패 {len(jp_failed)}건" if jp_failed else "")
        + f" · {_src_desc} (열 머리글 클릭 시 정렬)"
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
            f"⚠️ 블로그 {len(failed_items)}건 조회 실패 (429 재시도 소진) — "
            "새로고침으로 재시도하거나, 잠시 후 다시 시도하세요: "
            + ", ".join(kw for kw, _, _ in failed_items)
        )
    if jp_failed:
        st.warning(
            f"⚠️ 데이터 검색량 {len(jp_failed)}건 조회 실패 (429 재시도 소진) — "
            "새로고침으로 재시도하세요: " + ", ".join(jp_failed)
        )


# ═══════════════════════════════════════════════════════════════════════════
# 화면 5 — 체험단 양식 생성 (레뷰 빈 docx 템플릿 → 셀 채우기 → 미리보기·다운로드)
# ═══════════════════════════════════════════════════════════════════════════
# ★빈 양식(templates/revu_basic_template.docx)을 원본 로드 후 정해진 셀만 치환한다.
#   서식·사전안내문(표12)·표 구조는 src.revu_form 이 100% 보존한다(새로 그리지 않음).
def _char_counter(label: str, limit: int, key: str) -> str:
    """글자수 제한 text_input — 초과 시 빨간 경고 + 실시간 카운트.

    값은 session_state(key)로 관리 — value= 미사용(불러오기와 충돌 없게)."""
    val = st.text_input(label, key=key)
    n = len(val)
    if n > limit:
        st.markdown(
            f'<span style="color:#dc2626;font-size:12px;">⚠️ 현재 {n}/{limit}자 '
            f"— {n - limit}자 초과! 줄여주세요.</span>",
            unsafe_allow_html=True,
        )
    else:
        st.caption(f"현재 {n}/{limit}자")
    return val


def _apply_tracking_url(url: str) -> None:
    """추적 URL 을 제품링크 칸(revu_url)에 반영(버튼 on_click 콜백 — rerun 전 안전 시점)."""
    st.session_state["revu_url"] = url


# 키워드 추천: 같은 검색어 재호출 방지(1시간 캐시). 양식용 — 연관키워드+검색량만(가벼움).
@st.cache_data(ttl=3600, show_spinner="연관 키워드 수집 중 (검색광고)...")
def _load_reco_keywords(seed: str) -> dict:
    """검색어 → 연관 키워드+검색량 수집 후 금지어 분리. 반환 {clean, excluded}."""
    pairs = recommend_keywords(seed, limit=40)   # adapter 자동 생성(NAVER_AD_* 키)
    clean, excluded = partition_banned(pairs)
    return {"clean": clean, "excluded": excluded}


def _add_reco_to_field(field_key: str) -> None:
    """체크된 추천 키워드를 제목/본문 키워드 칸에 ★덧붙인다(on_click 콜백).

    중복은 merge_keywords 가 제거. 적용 후 체크박스는 해제한다."""
    reco = st.session_state.get("revu_reco", {}).get("clean", [])
    selected = [kw for kw, _ in reco if st.session_state.get(f"revu_reco_ck::{kw}")]
    if not selected:
        return
    st.session_state[field_key] = merge_keywords(
        st.session_state.get(field_key, ""), selected)
    for kw, _ in reco:
        st.session_state[f"revu_reco_ck::{kw}"] = False


# 블로그 제목 기반 키워드 추천: 같은 검색어 재호출 방지(1시간 캐시).
@st.cache_data(ttl=3600, show_spinner="블로그 글 제목 수집 중...")
def _load_blog_reco(seed: str) -> dict:
    """검색어 → 블로그 글 제목 수집 → 빈도 기반 키워드 추출(금지어 제외).

    반환 recommend_blog_keywords 결과({keywords, titles, excluded}). 키 미설정·429 등
    실패는 예외로 전파(호출부에서 경고만 — 검색광고 연관어는 그대로 살린다)."""
    _ensure_naver_env()   # st.secrets 접근으로 오염된 네이버 키 재확정(1단계 함정 대응)
    cid = os.environ.get("NAVER_CLIENT_ID", "").strip()
    csec = os.environ.get("NAVER_CLIENT_SECRET", "").strip()
    if not (cid and csec):
        raise RuntimeError(
            "NAVER_CLIENT_ID/SECRET 미설정 — 블로그 제목 추천을 쓰려면 .env 에 추가하세요."
        )
    return recommend_blog_keywords(
        seed, titles_fn=lambda q: fetch_blog_titles(q, cid, csec))


def _add_blog_reco_to_field(field_key: str) -> None:
    """체크된 블로그 제목 키워드를 제목/본문 키워드 칸에 ★덧붙인다(on_click 콜백).

    검색광고 연관어와 동일하게 merge_keywords 로 중복 제거. 적용 후 체크박스 해제."""
    reco = st.session_state.get("revu_blog_reco", {}).get("keywords", [])
    selected = [kw for kw, _ in reco if st.session_state.get(f"revu_blog_ck::{kw}")]
    if not selected:
        return
    st.session_state[field_key] = merge_keywords(
        st.session_state.get(field_key, ""), selected)
    for kw, _ in reco:
        st.session_state[f"revu_blog_ck::{kw}"] = False


def _render_reco_checkboxes(pairs, ck_prefix: str, label_fn) -> None:
    """추천 키워드를 구매 의도별로 분류·정렬해 체크박스로 렌더한다(두 섹션 공용).

    구매형(🟢)→중간(🟡)은 본문에 바로, 정보형(🔴)은 ★접힌 expander 안에 표시
    (완전히 숨기지 않음 — 시경이 직접 골라 담을 수 있게). 모든 키워드에 체크박스를
    만들어야 '추가' 콜백이 선택을 인식하므로, 정보형도 키만 expander 안에서 생성한다.

    pairs: [(kw, val), ...] / ck_prefix: 체크박스 key 접두("revu_reco_ck::" 등)
    label_fn: val → 괄호 안 표시 문자열(예: lambda v: f"검색량 {v:,}").
    """
    buys, mids, infos = [], [], []
    for kw, val in pairs:
        cat = classify_intent(kw)
        (buys if cat == "buy" else infos if cat == "info" else mids).append((kw, val))
    for kw, val in buys + mids:
        cat = classify_intent(kw)
        st.checkbox(
            f"{BADGE[cat]} {kw}  ({label_fn(val)})", key=f"{ck_prefix}{kw}")
    if infos:
        with st.expander(
            f"🔴 이미 구매한 사람이 찾는 키워드 (매출 연결 낮음) · {len(infos)}개",
            expanded=False,
        ):
            st.caption(
                "이미 제품을 산 뒤 사용법·교체방법을 찾는 검색어라 체험단 노출이 "
                "매출로 잘 이어지지 않습니다. 필요하면 직접 골라 담으세요.")
            for kw, val in infos:
                st.checkbox(
                    f"🔴 {kw}  ({label_fn(val)})", key=f"{ck_prefix}{kw}")


# AI 키워드 자동완성: (차종, 제품) 동일 입력 6시간 캐시 — API 재호출(비용) 차단.
@st.cache_data(ttl=21600, show_spinner="AI 키워드 생성 중 (Claude)...")
def _load_ai_keywords(vehicle: str, product: str) -> dict:
    """차종+제품 → Claude 구매형 키워드 생성 → 금지어 분리. 반환 {clean, excluded}.

    api_key 는 st.secrets 까지 보는 _secret_candidates 로 해석해 generate 에 주입
    (keyword_ai 는 순수 함수라 st.secrets 를 직접 안 봄). 실패는 generate 가 [] 로 흡수."""
    key = resolve_secret(_secret_candidates("ANTHROPIC_API_KEY"))
    kws = generate_ai_keywords(vehicle, product, api_key=key)
    pairs = [(kw, None) for kw in kws]
    clean, excluded = partition_banned(pairs)   # 금지어 제외(단일 출처 재사용)
    return {"clean": clean, "excluded": excluded}


def _add_ai_reco_to_field(field_key: str) -> None:
    """체크된 AI 키워드를 제목/본문 키워드 칸에 덧붙인다(중복 제거). 적용 후 체크 해제."""
    reco = st.session_state.get("revu_ai_reco", {}).get("clean", [])
    selected = [kw for kw, _ in reco if st.session_state.get(f"revu_ai_ck::{kw}")]
    if not selected:
        return
    st.session_state[field_key] = merge_keywords(
        st.session_state.get(field_key, ""), selected)
    for kw, _ in reco:
        st.session_state[f"revu_ai_ck::{kw}"] = False


def _apply_mission_block(car_model: str, product_name: str) -> None:
    """선택한 각도로 리치 미션 3줄을 미션 칸에 채운다(on_click 콜백 — 덮어씀).

    선택 라벨(revu_mission_angle) → 각도 key 역매핑 후 mission_block 생성.
    다른 각도를 골라 다시 누르면 다시 채워진다('다시채움')."""
    label = st.session_state.get("revu_mission_angle")
    angles = mission_angles(product_name)
    key = next((k for k, lab in angles if lab == label), angles[0][0])
    for i, line in enumerate(mission_block(car_model, product_name, key)):
        st.session_state[f"revu_mission_{i}"] = line


def _load_revu_form_file() -> None:
    """업로드 JSON 을 위젯 session_state 로 복원(file_uploader on_change 콜백 — 안전 시점).

    ★콜백은 rerun 의 위젯 생성 전에 돌아 session_state 설정이 안전(StreamlitAPIException 회피).
    손상·구버전 파일이어도 deserialize_form 이 죽지 않고 가능한 필드만 채운다."""
    up = st.session_state.get("revu_loader")
    if up is None:
        st.session_state["_revu_load_msg"] = None
        return
    try:
        raw = up.getvalue()
    except Exception:  # noqa: BLE001 — 업로드 객체 read 실패도 경고로만.
        st.session_state["_revu_load_msg"] = ("error", ["파일을 읽을 수 없습니다."])
        return
    values, warns = deserialize_form(raw)
    for k, v in values.items():
        st.session_state[k] = v
    st.session_state["_revu_load_msg"] = (("ok" if values else "error"), warns)


def render_revu_form() -> None:
    _inject_css()
    st.title("체험단 양식 — 레뷰 네이버 베이직")
    st.caption(
        "빈 표준 양식(레뷰 베이직)에 입력값을 채워 docx 를 생성합니다. "
        "서식·사전 안내문은 원본 그대로 보존 — 정해진 빈칸만 채웁니다."
    )

    if not REVU_TEMPLATE_PATH.exists():
        st.error(
            f"빈 양식 템플릿을 찾을 수 없습니다: {REVU_TEMPLATE_PATH}\n\n"
            "templates/revu_basic_template.docx 파일을 넣어주세요."
        )
        return

    # 위젯 기본값을 session_state 에 1회 주입 — 모든 양식 위젯은 value= 대신 key 로만
    # 관리한다(불러오기로 session_state 를 설정해도 value= 와 충돌·경고 없이 반영되게).
    for _k, _v in revu_form_defaults().items():
        st.session_state.setdefault(_k, _v)

    # ── 양식 저장 / 불러오기 (파일 — ★Streamlit Cloud 영구저장 아님) ──
    with st.expander("💾 양식 저장 / 📂 불러오기", expanded=False):
        st.caption(
            "작성한 값을 JSON 파일로 내려받아 보관하고, 다음에 올리면 그대로 채워집니다. "
            "앱이 아니라 ★시경 PC에 저장 — 앱 재시작에도 안 날아갑니다."
        )
        _cur = {k: st.session_state.get(k, d) for k, d in revu_form_defaults().items()}
        col_save, col_load = st.columns(2)
        with col_save:
            st.download_button(
                "💾 양식 저장 (JSON 다운로드)",
                data=serialize_form(_cur).encode("utf-8"),
                file_name=save_filename_json(_cur),
                mime="application/json",
                key="revu_save_btn",
            )
        with col_load:
            st.file_uploader(
                "📂 양식 불러오기 (JSON 업로드)", type=["json"],
                key="revu_loader", on_change=_load_revu_form_file)
        _msg = st.session_state.get("_revu_load_msg")
        if _msg:
            _kind, _warns = _msg
            if _kind == "ok":
                st.success("✅ 불러왔습니다. 필요한 부분만 수정하세요.")
            else:
                st.error("불러오기 실패: " + (" / ".join(_warns) if _warns else "알 수 없는 오류"))
            for _w in _warns:
                st.warning("⚠️ " + _w)

    # ── Step 1. 콘텐츠 타입 · 옵션(드롭다운 위젯 → 선택 텍스트로 양식에 박힘) ──
    st.subheader("1. 콘텐츠 타입 · 옵션")
    col_ct, col_pc, col_ug = st.columns(3)
    with col_ct:
        content_type = st.radio("콘텐츠 타입", ["블로그", "클립"], key="revu_content_type")
    with col_pc:
        purchase_combine = st.radio(
            "구매평 결합", ["아니오", "예"], key="revu_purchase_combine")
    with col_ug:
        urgent = st.radio("긴급 진행", ["아니오", "예"], key="revu_urgent")

    # ── Step 2. 캠페인 제목/부제목 (글자수 제한) ──
    st.subheader("2. 캠페인 제목 · 부제목")
    col_t, col_s = st.columns(2)
    with col_t:
        campaign_title = _char_counter(
            f"캠페인 제목 (최대 {TITLE_MAX}자)", TITLE_MAX, "revu_title")
    with col_s:
        campaign_subtitle = _char_counter(
            f"캠페인 부제목 (최대 {SUBTITLE_MAX}자)", SUBTITLE_MAX, "revu_subtitle")

    # ── Step 3. 제품 정보 ──
    st.subheader("3. 제품 정보")
    col_c, col_p = st.columns(2)
    with col_c:
        car_model = st.text_input(
            "차종 (선택)", key="revu_car",
            help="에어컨필터·와이퍼 등 차종이 들어가는 제품만. 비우면 차종 없이 진행.")
    with col_p:
        product_name = st.text_input("제품명", key="revu_product")
    col_q, col_n = st.columns(2)
    with col_q:
        provide_qty = st.text_input(
            "제공수량 (예: EV5 에어컨필터 P17 2개)", key="revu_qty",
            help="제품 스펙 + 수량 단위(\"2개\" 등)까지 직접 입력하세요. 양식에 그대로 들어갑니다.")
    with col_n:
        recruit_count = st.number_input(
            "모집인원", min_value=1, max_value=999, step=1, key="revu_recruit")

    # ── Step 4. 키워드 (+ 검색광고 연관키워드 추천) ──
    st.subheader("4. 키워드")

    with st.expander("🔍 키워드 추천 (검색광고 연관어 + 블로그 제목)", expanded=False):
        st.caption(
            "차종·제품 기반 ①검색광고 연관키워드(월검색량)와 ②블로그 글 제목 기반 키워드를 "
            "함께 가져옵니다. 신차(예: EV5)는 연관어가 빈약해 블로그 제목으로 보완합니다. "
            "체크 → 제목/본문 키워드 칸에 덧붙이기(중복 자동 제거). 제목 3개·본문 5개까지 권장."
        )
        # 차종·제품명이 있으면 "{차종} {제품}" 기본값 자동 채움(수정 가능).
        _reco_default = " ".join(p for p in (car_model.strip(), product_name.strip()) if p)
        reco_seed = st.text_input(
            "추천 받을 검색어", value=_reco_default, key="revu_reco_seed",
            help='예: "EV5 에어컨필터". 차종·제품 입력 시 자동 채움.')
        if st.button("추천 받기", key="revu_reco_btn"):
            _seed = reco_seed.strip()
            if _seed:
                # ① 검색광고 연관어(현행) — 실패해도 ②블로그를 막지 않게 독립 try.
                try:
                    st.session_state["revu_reco"] = _load_reco_keywords(_seed)
                except Exception as e:  # noqa: BLE001 — 원인 명시(429/키 미설정 등)
                    st.error(f"검색광고 연관어 추천 실패: {type(e).__name__}: {e}")
                # ② 블로그 제목 기반(신규) — 실패 시 ★경고만(연관어는 그대로 살림).
                try:
                    st.session_state["revu_blog_reco"] = _load_blog_reco(_seed)
                except Exception as e:  # noqa: BLE001
                    st.session_state["revu_blog_reco"] = {
                        "error": f"{type(e).__name__}: {e}"}
            else:
                st.warning("검색어를 입력하세요.")

        # ── 🤖 AI 키워드 자동완성(Claude) — 이미 입력된 차종·제품 사용 ──
        st.markdown("**🤖 AI 키워드 자동완성**")
        _ai_key = resolve_secret(_secret_candidates("ANTHROPIC_API_KEY"))
        _ai_product = product_name.strip()
        _ai_disabled = not (_ai_key and _ai_product)
        if not _ai_key:
            st.caption("⚠️ AI 자동완성을 쓰려면 Secrets 에 ANTHROPIC_API_KEY 가 필요합니다.")
        elif not _ai_product:
            st.caption("위 제품명을 입력하면 AI 자동완성을 쓸 수 있습니다(차종은 선택).")
        else:
            st.caption(
                f"‘{(car_model.strip() + ' ' + _ai_product).strip()}’ 기반 구매형 키워드를 "
                "Claude 가 생성합니다. 🟢구매형 우선 / 🟡중간 / 🔴정보형(접힘).")
        if st.button("AI로 키워드 생성", key="revu_ai_btn", disabled=_ai_disabled):
            try:
                st.session_state["revu_ai_reco"] = _load_ai_keywords(
                    car_model.strip(), _ai_product)
            except Exception as e:  # noqa: BLE001 — 키 미설정 등은 경고만(앱 유지)
                st.warning(f"AI 키워드 생성 실패: {type(e).__name__}: {e}")

        _ai = st.session_state.get("revu_ai_reco", {})
        _ai_clean = _ai.get("clean", [])
        _ai_excluded = _ai.get("excluded", [])
        if _ai_clean:
            _n_aichecked = sum(
                1 for kw, _ in _ai_clean if st.session_state.get(f"revu_ai_ck::{kw}"))
            st.caption(f"AI 키워드 {len(_ai_clean)}개 · 선택 {_n_aichecked}개")
            _render_reco_checkboxes(_ai_clean, "revu_ai_ck::", lambda v: "AI")
            col_ait, col_aib = st.columns(2)
            col_ait.button(
                "➕ 제목키워드에 추가", key="revu_ai_add_title",
                on_click=_add_ai_reco_to_field, args=("revu_titlekw",),
                help="제목키워드는 3개까지 권장.")
            col_aib.button(
                "➕ 본문키워드에 추가", key="revu_ai_add_body",
                on_click=_add_ai_reco_to_field, args=("revu_bodykw",),
                help="본문키워드는 5개까지 권장.")
            if _ai_excluded:
                st.caption("⚠️ 금지어 의심으로 제외됨: " + ", ".join(_ai_excluded))
        elif "revu_ai_reco" in st.session_state:
            st.info("AI 키워드가 없습니다(빈 결과). 제품명을 더 구체적으로 입력해 보세요.")

        # ── ① 검색광고 연관키워드(현행) ──
        st.markdown("**🔎 검색광고 연관키워드**")
        _reco = st.session_state.get("revu_reco", {})
        _clean = _reco.get("clean", [])
        _excluded = _reco.get("excluded", [])
        if _clean:
            _n_checked = sum(
                1 for kw, _ in _clean if st.session_state.get(f"revu_reco_ck::{kw}"))
            st.caption(
                f"연관 키워드 {len(_clean)}개 · 선택 {_n_checked}개 "
                "· 🟢구매형 우선 / 🟡중간 / 🔴정보형(접힘)")
            _render_reco_checkboxes(
                _clean, "revu_reco_ck::", lambda v: f"검색량 {v:,}")
            col_at, col_ab = st.columns(2)
            col_at.button(
                "➕ 제목키워드에 추가", key="revu_add_title",
                on_click=_add_reco_to_field, args=("revu_titlekw",),
                help="제목키워드는 3개까지 권장.")
            col_ab.button(
                "➕ 본문키워드에 추가", key="revu_add_body",
                on_click=_add_reco_to_field, args=("revu_bodykw",),
                help="본문키워드는 5개까지 권장.")
            if _excluded:
                st.caption("⚠️ 금지어 의심으로 제외됨: " + ", ".join(_excluded))
        elif "revu_reco" in st.session_state:
            st.info("연관 키워드가 없습니다(검색량 0이거나 결과 없음). 신차라면 아래 블로그 제목을 참고하세요.")

        # ── ② 블로그 제목 기반 키워드(신규) ──
        st.markdown("**📝 블로그 제목 기반 키워드**")
        _blog = st.session_state.get("revu_blog_reco", {})
        if _blog.get("error"):
            st.warning(
                "⚠️ 블로그 제목 추천을 가져오지 못했습니다(" + _blog["error"] + "). "
                "검색광고 연관어는 위에서 그대로 쓸 수 있습니다.")
        _blog_kws = _blog.get("keywords", [])
        _blog_titles = _blog.get("titles", [])
        _blog_excluded = _blog.get("excluded", [])
        # 제품 관련성 필터 결과 안내(차량 일반 글 제외됨).
        _n_prod = _blog.get("n_product_titles")
        _n_tot = _blog.get("n_total_titles")
        if _n_tot and _n_prod is not None and _n_prod < _n_tot:
            if _n_prod == 0:
                st.warning(
                    "⚠️ 제품 관련 블로그 글을 찾지 못했습니다(신차 등으로 글이 적음). "
                    "검색광고 연관어나 직접 입력을 활용하세요.")
            else:
                st.caption(
                    f"🎯 제품 관련 제목 {_n_prod}/{_n_tot}개에서만 키워드를 뽑았습니다"
                    "(보조금·연비 등 차량 일반 글 제외).")
        if _blog_kws:
            _n_bchecked = sum(
                1 for kw, _ in _blog_kws if st.session_state.get(f"revu_blog_ck::{kw}"))
            st.caption(
                f"제목에서 뽑은 키워드 {len(_blog_kws)}개 · 선택 {_n_bchecked}개 "
                "(괄호 = 등장 제목 수). 🟢구매형 우선 / 🟡중간 / 🔴정보형(접힘). "
                "형태소분석 없는 빈도 추출이라 노이즈가 있을 수 있어요.")
            _render_reco_checkboxes(
                _blog_kws, "revu_blog_ck::", lambda v: f"제목 {v}개")
            col_bt, col_bb = st.columns(2)
            col_bt.button(
                "➕ 제목키워드에 추가", key="revu_blog_add_title",
                on_click=_add_blog_reco_to_field, args=("revu_titlekw",),
                help="제목키워드는 3개까지 권장.")
            col_bb.button(
                "➕ 본문키워드에 추가", key="revu_blog_add_body",
                on_click=_add_blog_reco_to_field, args=("revu_bodykw",),
                help="본문키워드는 5개까지 권장.")
            if _blog_excluded:
                st.caption("⚠️ 금지어 의심으로 제외됨: " + ", ".join(_blog_excluded))
        elif "revu_blog_reco" in st.session_state and not _blog.get("error"):
            st.info("블로그 제목에서 뽑을 키워드가 없습니다(검색 결과 없음).")

        # 실제 블로그 제목 원문 — 시경이 직접 참고(접어서 표시).
        if _blog_titles:
            with st.expander(f"📰 이런 블로그 제목들이 있습니다 ({len(_blog_titles)}개)", expanded=False):
                for t in _blog_titles:
                    st.markdown(f"- {t}")

    col_tk, col_bk = st.columns(2)
    with col_tk:
        title_keywords = st.text_area(
            "제목키워드 (1~3개, 콤마 구분)", key="revu_titlekw", height=80)
    with col_bk:
        body_keywords = st.text_area(
            "본문키워드 (3~5개, 콤마 구분)", key="revu_bodykw", height=80)

    # 금지어 가벼운 경고(질병명·절대표현). 완벽한 필터 아님 — 경고만.
    banned = find_banned_words(title_keywords, body_keywords, campaign_title, campaign_subtitle)
    if banned:
        st.warning(
            "⚠️ 금지어/절대표현 의심: **" + ", ".join(banned) + "** — 양식 규칙상 제외하세요.\n\n"
            "※ 타브랜드·연예인명은 자동 판별이 어려우니 직접 확인 바랍니다."
        )

    # ── Step 5. 제품링크 (+ 네이버 유입 추적 URL 생성) ──
    st.subheader("5. 제품링크")
    product_url = st.text_input(
        "제품링크 URL", key="revu_url",
        help="아래 '네이버 유입 추적 URL 생성'으로 만든 URL을 여기에 바로 넣을 수 있습니다.")

    with st.expander("🔗 네이버 유입 추적 URL 생성 (nt_)", expanded=False):
        st.caption(
            "제품 URL + 추적 파라미터를 조립합니다(단축 없음). "
            "nt_source·nt_medium 은 영문/숫자/-,_,. 만(한글·공백 금지, 필수). "
            'URL 에 이미 "?"가 있으면 자동으로 "&"로 이어붙입니다.'
        )
        track_base = st.text_input(
            "제품 URL", key="revu_track_base",
            help="예: https://m.site.naver.com/2aAvQ 또는 스마트스토어 URL")
        col_s, col_m = st.columns(2)
        with col_s:
            nt_source = st.text_input("nt_source (필수)", key="revu_nt_source")
        with col_m:
            nt_medium = st.text_input("nt_medium (필수)", key="revu_nt_medium")
        col_d, col_k = st.columns(2)
        with col_d:
            nt_detail = st.text_input("nt_detail (선택)", key="revu_nt_detail")
        with col_k:
            nt_keyword = st.text_input(
                "nt_keyword (선택)", key="revu_nt_keyword",
                help="제품·차종 (예: EV5에어컨필터). 한글 허용. 비었으면 아래 버튼으로 차종 채움.")
        if car_model.strip() and not st.session_state.get("revu_nt_keyword"):
            st.button(
                "차종을 nt_keyword 로", key="revu_nt_kw_from_car",
                on_click=lambda: st.session_state.__setitem__(
                    "revu_nt_keyword", car_model.strip()))

        track_url, track_errors = assemble_tracking_url(
            track_base, nt_source, nt_medium, nt_detail, nt_keyword)
        if track_errors:
            for e in track_errors:
                st.markdown(
                    f'<span style="color:#dc2626;font-size:12.5px;">⚠️ {e}</span>',
                    unsafe_allow_html=True,
                )
        if track_url:
            st.code(track_url, language="text")
            parts = [f"nt_source={nt_source}", f"nt_medium={nt_medium}"]
            if nt_detail:
                parts.append(f"nt_detail={nt_detail}")
            if nt_keyword:
                parts.append(f"nt_keyword={nt_keyword}")
            st.caption(" · ".join(parts))
            st.button(
                "⬇️ 이 URL을 제품링크 칸에 넣기",
                key="revu_apply_track",
                on_click=_apply_tracking_url, args=(track_url,),
            )
        else:
            st.info("필수값(제품 URL·nt_source·nt_medium)을 채우면 추적 URL이 생성됩니다.")

    # ── Step 6. 미션 (각도 선택 → 실데이터 기반 5단 미션 자동 채움, 수정 가능) ──
    st.subheader(f"6. {content_type} 미션 (1·2·3)")
    if car_model.strip():
        _angles = mission_angles(product_name)
        _angle_labels = [lab for _key, lab in _angles]
        # 제품군이 바뀌면 이전 라벨이 새 옵션에 없을 수 있어 초기화(StreamlitAPIException 방지).
        if st.session_state.get("revu_mission_angle") not in _angle_labels:
            st.session_state["revu_mission_angle"] = _angle_labels[0]
        col_ang, col_btn = st.columns([2, 1])
        with col_ang:
            st.selectbox(
                "미션 각도 선택", _angle_labels, key="revu_mission_angle",
                help="제품 특성에 맞는 후기 각도. 고르고 옆 버튼을 누르면 미션이 채워집니다.")
        with col_btn:
            st.button(
                "✨ 선택한 각도로 미션 채우기", key="revu_fill_missions",
                on_click=_apply_mission_block, args=(car_model, product_name),
                help="기존 미션을 덮어씁니다. 다른 각도로 다시 누르면 다시 채워집니다.")
        st.caption(
            "각도를 고르고 채우면 5단 미션이 자동 작성됩니다. [필수 언급] 칸의 셀링포인트는 "
            "실제 제품 사실로 꼭 확인·수정하세요(AI가 채우지 않음).")
    missions = []
    for i in range(3):
        missions.append(st.text_area(f"미션 {i + 1}", key=f"revu_mission_{i}", height=120))

    # ── Step 7. 담당자 (기본값 자동 채움, 수정 가능) ──
    st.subheader("7. 담당자 정보")
    col_m1, col_m2, col_m3 = st.columns(3)
    with col_m1:
        manager_name = st.text_input("성함", key="revu_mgr_name")
    with col_m2:
        manager_phone = st.text_input("연락처", key="revu_mgr_phone")
    with col_m3:
        manager_email = st.text_input("이메일", key="revu_mgr_email")

    data = RevuFormData(
        content_type=content_type,
        purchase_combine=purchase_combine,
        urgent=urgent,
        campaign_title=campaign_title,
        campaign_subtitle=campaign_subtitle,
        car_model=car_model,
        product_name=product_name,
        provide_qty=provide_qty,
        recruit_count=int(recruit_count),
        title_keywords=title_keywords.strip(),
        body_keywords=body_keywords.strip(),
        product_url=product_url,
        missions=missions,
        manager_name=manager_name,
        manager_phone=manager_phone,
        manager_email=manager_email,
    )

    # ── 미리보기 ──
    st.divider()
    st.subheader("📋 미리보기 — 양식 칸에 들어갈 내용")
    mission_label = f"{content_type} 미션"
    preview_rows = [
        ("콘텐츠 타입", content_type),
        ("구매평 결합", purchase_combine),
        ("긴급 진행", urgent),
        ("캠페인 제목", campaign_title or "—"),
        ("캠페인 부제목", campaign_subtitle or "—"),
        ("차종", car_model or "(없음)"),
        ("제품명", product_name or "—"),
        ("제공수량", provide_qty or "—"),
        ("모집인원", f"{int(recruit_count)}명"),
        ("제목키워드", title_keywords or "—"),
        ("본문키워드", body_keywords or "—"),
        ("제품링크", product_url or "—"),
        (mission_label, " / ".join(m for m in missions if m) or "—"),
        ("담당자", f"{manager_name} · {manager_phone} · {manager_email}"),
    ]
    _highlight_table(
        "생성될 docx 내용 확인",
        ["항목", "값"],
        [[k, v] for k, v in preview_rows],
        align=["left", "left"],
    )
    st.caption("※ 표12(사전 안내문)·표 서식은 원본 그대로 보존됩니다.")

    # ── docx 생성·다운로드 ──
    st.divider()
    over_title = len(campaign_title) > TITLE_MAX
    over_sub = len(campaign_subtitle) > SUBTITLE_MAX
    if over_title or over_sub:
        st.error("글자수 제한 초과 항목이 있습니다 — 제목/부제목을 줄인 뒤 생성하세요.")
    try:
        docx_bytes = build_revu_docx(data)
        st.download_button(
            "📥 체험단 양식 docx 다운로드",
            data=docx_bytes,
            file_name=suggest_filename(data),
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            disabled=(over_title or over_sub),
        )
    except Exception as e:  # noqa: BLE001 — 원인 명시(템플릿 누락 등, 조용한 폴백 금지)
        st.error(f"docx 생성 실패: {type(e).__name__}: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# 사이드바 화면 선택 → 디스패치
# ═══════════════════════════════════════════════════════════════════════════
st.sidebar.markdown("**화면 선택**")
_SCREEN = st.sidebar.radio(
    "화면 선택",
    ["차종 수요", "계절 제품", "키워드 탐색기", "체험단 타겟", "체험단 양식"],
    label_visibility="collapsed",
    key="_screen_select",
)
st.sidebar.divider()

if _SCREEN == "차종 수요":
    render_car_demand()
elif _SCREEN == "계절 제품":
    render_seasonal()
elif _SCREEN == "체험단 타겟":
    render_teamp()
elif _SCREEN == "체험단 양식":
    render_revu_form()
else:
    render_keyword_explorer()
