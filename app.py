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
from src.core.search_volume import (
    FIELD_COMP_IDX,
    FIELD_MONTHLY_MOBILE,
    FIELD_MONTHLY_PC,
    dedupe_relkeywords,
    member_volume,
)

st.set_page_config(page_title="dgk-product-scout", layout="wide")


def _fmt_vol_display(vol: int, low: bool) -> str:
    """표시 전용 검색량 포맷. low(원래 '< 10')면 '<10', 아니면 천단위 콤마.
    ★숫자를 만들어내지 않는다 — '<10' 은 네이버가 정확값을 안 주는 항목의 표시일 뿐."""
    return "<10" if low else f"{vol:,}"


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
    st.title("차종 수요 — 모델별 규모 + 추세")
    st.caption(
        "부품 시드 수확 → 차종 인식 → 모델별 합산 규모(C-3) + 데이터랩 월별 추세(C-4). "
        "규모·추세 별도 컬럼 — 단일 매력도 점수 없음. (단순 표시 — 등록 대조 없음)"
    )

    col_btn, col_note = st.columns([1, 4])
    with col_btn:
        if st.button("🔄 새로고침", key="car_refresh",
                     help="캐시를 비우고 검색광고+데이터랩을 다시 호출합니다."):
            st.cache_data.clear()
            st.rerun()
    with col_note:
        st.caption(
            f"API 응답은 1시간 캐시됩니다(클릭마다 재호출 방지 · rate limit 보호). "
            f"컷 MODEL_MIN_VOLUME={config.MODEL_MIN_VOLUME:,} · "
            f"추세 {config.TREND_RECENT_MONTHS}개월÷{config.TREND_BASELINE_MONTHS}개월."
        )

    st.info(_LIMITS_MD)

    try:
        rows, trends, note, members_map = _load_car_demand()
    except Exception as e:  # noqa: BLE001 — 원인 명시(키 미설정/API 실패 등, 조용한 폴백 금지)
        st.error(f"차종 수요 수집 실패: {type(e).__name__}: {e}")
        return

    if note:
        st.warning(note)
    if not rows:
        st.info("표시할 모델이 없습니다(수확 결과가 컷 미만이거나 비었음).")
        return

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
    f"· 추천 필터 = 규모 ≥ {config.MARKET_SIZE_THRESHOLD:,} AND 계절성 ≥ {sc.FILTER_MIN_INDEX}. "
    "발주 데드라인은 미포함."
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
}

_WINTER_COLUMN_CONFIG = {
    "겨울 시드": st.column_config.TextColumn("겨울 시드"),
    "기기군 합산": st.column_config.NumberColumn("기기군 합산", format="localized"),
    "멤버수": st.column_config.NumberColumn("멤버수", format="localized"),
}


def _season_row(r: dict) -> dict:
    """계절 rows → st.dataframe dict. 규모는 sc._fmt_volume 로 '<10'/'확인필요'/콤마 표시."""
    return {
        "제품": r["keyword"],
        "시즌": r["season"],
        "계절성지수": round(r["index"], 2),
        "정점/상승월": f"{r['peak_month']}월/{r['rising_month']}월",
        "월 검색량(단일 키워드)": sc._fmt_volume(r["volume"]),
        "안정성": r["stability"],
    }


def render_seasonal() -> None:
    st.title("계절 제품 — 계절성 + 규모(단일 키워드)")
    st.caption(
        "데이터랩 계절성(모양) + 검색광고 단일 키워드 규모. 규모·계절성 별도 컬럼 — "
        "단일 매력도 점수 없음."
    )

    # 화면 전용 사이드바 입력(정렬·필터).
    sort_label = st.sidebar.radio("정렬", ["규모순", "계절성순"], key="season_sort")
    apply_filter = st.sidebar.toggle(
        f"추천 필터 (규모≥{config.MARKET_SIZE_THRESHOLD:,} AND 계절성≥{sc.FILTER_MIN_INDEX})",
        value=True, key="season_filter", help="끄면 전체 제품을 표시합니다.",
    )

    col_btn, col_note = st.columns([1, 4])
    with col_btn:
        if st.button("🔄 새로고침", key="season_refresh",
                     help="캐시를 비우고 데이터랩+검색광고를 다시 호출합니다."):
            st.cache_data.clear()
            st.rerun()
    with col_note:
        st.caption("API 응답은 1시간 캐시됩니다(클릭마다 재호출 방지 · rate limit 보호).")

    st.info(_SEASON_LIMITS_MD)

    try:
        rows, winter, note = _load_seasonal()
    except Exception as e:  # noqa: BLE001 — 원인 명시(키 미설정/API 실패 등, 조용한 폴백 금지)
        st.error(f"계절 제품 수집 실패: {type(e).__name__}: {e}")
        return

    if note:
        st.warning(note)
    if not rows:
        st.info("표시할 계절 제품이 없습니다(계절 시계열이 모두 비어 있음).")
        return

    sort_by = "seasonality" if sort_label == "계절성순" else "scale"

    def passes(r: dict) -> bool:  # 추천 필터 = 규모 AND 계절성 (임계는 config/seasonal 그대로).
        return (sc._vol_num(r["volume"]) >= config.MARKET_SIZE_THRESHOLD
                and r["index"] >= sc.FILTER_MIN_INDEX)

    kept = [r for r in rows if (not apply_filter) or passes(r)]
    shown = sc._sorted(kept, sort_by)  # 파이프라인 정렬 헬퍼 재사용

    st.caption(
        f"{sort_label} · {'추천필터 ON' if apply_filter else '전체'} · "
        f"{len(shown)}/{len(rows)}개 (열 머리글 클릭 시 정렬)"
    )
    st.dataframe(
        [_season_row(r) for r in shown],
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
    st.title("키워드 탐색기")
    st.caption(
        "정해진 시드 외에 즉석으로 키워드를 검색량과 함께 확인하는 탐색 도구입니다. "
        "좋다/나쁘다 판정은 하지 않습니다 — 사실만 보여줍니다."
    )

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
            st.cache_data.clear()
            st.rerun()
    with col_note:
        st.caption("검색광고 키워드도구(NAVER_AD_*) 사용 · 결과 1시간 캐시.")

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
# 사이드바 화면 선택 → 디스패치
# ═══════════════════════════════════════════════════════════════════════════
_SCREEN = st.sidebar.radio("화면 선택", ["차종 수요", "계절 제품", "키워드 탐색기"])
st.sidebar.divider()

if _SCREEN == "차종 수요":
    render_car_demand()
elif _SCREEN == "계절 제품":
    render_seasonal()
else:
    render_keyword_explorer()
