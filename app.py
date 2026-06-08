"""
app.py — dgk-product-scout 대시보드 (Streamlit).

탭 구성:
  · 카테고리 발굴 (Layer 1) — 기존 발굴 랭킹(Mock/CSV/Naver).
  · 차종 수요 — Phase C 차종 수요 스캐너(규모 C-3 + 추세 C-4). 단순 표시(등록 대조 없음).

실행: streamlit run app.py

표시 원칙(스펙 6절 #6): 화면 숫자는 전부 반올림. 차종 수요 표는 규모·추세 별도 컬럼 —
단일 매력도 점수로 섞지 않는다(터미널 scripts/car_demand.py 와 동일).
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile

import streamlit as st

try:
    # .env 의 네이버 API 키를 환경변수로 로드(없어도 무시 — 키 검증은 어댑터가 명시 처리).
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# scripts/car_demand.py(터미널 표 포맷·추세 수집 로직)를 재사용하기 위한 경로 추가.
# 표 포맷 로직을 앱에 중복 구현하지 않고 파이프라인 결과를 받아 st.dataframe 로 렌더한다.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "scripts"))

import config
import car_demand as car_cli  # scripts/car_demand.py — fetch_trends/_sort_rows/_trend_cell/_low_conf
from src import diagnostics
from src.adapters.csv_adapter import CSVAdapter
from src.adapters.mock_adapter import MockAdapter
from src.adapters.naver_adapter import NaverAdapter
from src.core.car_demand import harvest_models, model_member_keywords, rank_models
from src.core.car_models import load_car_models
from src.ranking import discover

st.set_page_config(page_title="dgk-product-scout", layout="wide")


# ───────────────────────────────────────────────────────────────────────────
# 탭 1 — 카테고리 발굴 (Layer 1, 기존 화면 그대로)
# ───────────────────────────────────────────────────────────────────────────
def render_category_discovery() -> None:
    st.title("Layer 1 — 카테고리 발굴 랭킹")
    st.caption(
        "어떤 제품군에 진입할까? · 큰 시장 × 가치 싸움 우선 · 광고 싸움(함정)은 기본 제외"
    )

    # --- 데이터 소스 선택 ---
    source = st.sidebar.radio(
        "데이터 소스",
        ["Mock(테스트)", "CSV(오빠두 연관검색어)", "Naver(검색광고 API)"],
        help=(
            "CSV = 오빠두 연관검색어 엑셀(키워드 1개씩, 발굴 불가). "
            "Naver = 키워드도구 API 자동 호출로 시드→연관키워드 발굴(실데이터)."
        ),
    )

    # 보완사항 1: 함정(광고 싸움) 기본 제외 + '함정 포함 보기' 토글.
    include_trap = st.sidebar.toggle(
        "함정 포함 보기",
        value=False,
        help="광고 싸움(함정)·무시 분면 카테고리를 랭킹에 다시 포함해 봅니다. 기본은 제외.",
    )

    candidates = None
    if source.startswith("Mock"):
        st.caption("⚠️ MockAdapter — 가짜 데이터(개발/테스트용).")
        candidates = discover(MockAdapter(), include_trap=include_trap)
    elif source.startswith("Naver"):
        st.caption("⚠️ 소모품 필터는 근사(부분 문자열)이며 오매칭 가능 — 결과는 사람 확인 필요.")
        seed_text = st.sidebar.text_area(
            "시드 키워드(줄바꿈 또는 쉼표로 구분)",
            help="예: 로봇청소기, 공기청정기, 전동칫솔. 각 시드의 연관키워드를 자동 수확합니다.",
        )
        seeds = [s.strip() for s in seed_text.replace(",", "\n").splitlines() if s.strip()]
        if not seeds:
            st.info("사이드바에 시드 키워드를 입력하세요.")
        else:
            try:
                candidates = discover(NaverAdapter(seeds), include_trap=include_trap)
            except Exception as e:  # noqa: BLE001 — 원인 명시(키 미설정/API 실패 등, 조용한 폴백 금지)
                st.error(f"Naver API 처리 실패: {type(e).__name__}: {e}")
                return
    else:
        st.caption("⚠️ 소모품 필터는 근사(부분 문자열)이며 오매칭 가능 — 결과는 사람 확인 필요.")
        uploaded = st.sidebar.file_uploader("연관검색어 CSV 업로드", type=["csv"])
        if uploaded is None:
            st.info("사이드바에서 CSV 파일을 업로드하세요.")
        else:
            # 업로드 파일을 임시 경로로 저장해 CSVAdapter(경로 기반)에 전달.
            with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
                tmp.write(uploaded.getvalue())
                tmp_path = tmp.name
            try:
                candidates = discover(CSVAdapter(tmp_path), include_trap=include_trap)
            except Exception as e:  # noqa: BLE001 — 사용자에게 원인 명시(조용한 폴백 금지)
                st.error(f"CSV 처리 실패: {type(e).__name__}: {e}")
                return

    if candidates is None:
        return  # 아직 입력 대기(예: CSV 미업로드) — 아래 렌더링 생략.

    st.subheader(f"랭킹 ({len(candidates)}개)")

    if not candidates:
        st.info("표시할 카테고리가 없습니다.")
    else:
        rows = []
        for rank, c in enumerate(candidates, start=1):
            s = c.signal_scores
            rows.append(
                {
                    "순위": rank,
                    "카테고리": c.category_name,
                    "4분면": c.winnability,
                    "시장규모(추정)": c.market_size_est,
                    "발굴패턴": c.discovery_pattern,
                    "랭킹점수": round(c.ranking_score, 2),
                    "광고싸움": "예" if c.is_ad_war else "아니오",
                    "왜 기회인가": c.why_opportunity,
                    "진입난이도": c.entry_difficulty,
                }
            )
        st.dataframe(rows, use_container_width=True, hide_index=True)

        st.subheader("신호 8개 점수 (0.0~1.0)")
        signal_labels = {
            "signal_1_base_device_popularity": "1·기기인기",
            "signal_2_consumable_exists": "2·소모품존재",
            "signal_3_price_gap": "3·가격갭",
            "signal_4_repurchase_cycle": "4·재구매주기",
            "signal_5_compat_competition": "5·경쟁공백",
            "signal_6_our_capability": "6·역량적합",
            "signal_7_market_size": "7·시장규모",
            "signal_8_winnability": "8·진입가능성",
        }
        signal_rows = []
        for c in candidates:
            row = {"카테고리": c.category_name}
            for key, label in signal_labels.items():
                row[label] = round(c.signal_scores.get(key, 0.0), 2)
            signal_rows.append(row)
        st.dataframe(signal_rows, use_container_width=True, hide_index=True)

        # -------------------------------------------------------------------
        # 🔍 디버그: 원본값(정규화 전) + 시장규모 경계 점검
        #   정규화 점수만으로는 경계 적절성을 못 보므로 원본 검색량/경쟁정도/노출광고수를
        #   점수와 나란히 보여주고, config 경계 대비 실제 분포를 비교한다.
        # -------------------------------------------------------------------
        with st.expander("🔍 디버그 — 기기군 합산 검색량 & 시장규모 경계 점검", expanded=True):
            # 벤치마크(대표님 성공사례 기기군 합산) — 후보 합산이 어느 수준인지 가늠용.
            BENCHMARKS = {"와이퍼": 179_730, "에어컨필터": 346_520}

            # (1) 기기군 합산 ↔ 정규화 점수 나란히 + 벤치마크 대비.
            st.markdown("**기기군 합산 검색량 vs 정규화 점수**")
            st.caption(
                f"벤치마크(기기군 합산): 와이퍼 {BENCHMARKS['와이퍼']:,} · "
                f"에어컨필터 {BENCHMARKS['에어컨필터']:,} · '대' 경계 "
                f"{config.MARKET_SIZE_LARGE_SEARCHVOL:,}"
            )
            debug_rows = []
            for c in candidates:
                s = c.signal_scores
                agg = s.get("_raw_search_volume") or 0
                debug_rows.append(
                    {
                        "카테고리": c.category_name,
                        "기기군 합산 월검색수": agg,
                        "와이퍼 대비": f"{agg / BENCHMARKS['와이퍼']:.0%}" if agg else "-",
                        "신호7 점수": round(s.get("signal_7_market_size", 0.0), 3),
                        "시장규모": c.market_size_est,
                        "기준": s.get("_market_size_basis"),
                        "경쟁정도(compIdx)": s.get("_raw_comp_idx"),
                        "노출광고수(plAvgDepth)": s.get("_raw_avg_ad_depth"),
                        "신호8 점수": round(s.get("signal_8_winnability", 0.0), 3),
                        "광고싸움": "예" if c.is_ad_war else "아니오",
                    }
                )
            st.dataframe(debug_rows, use_container_width=True, hide_index=True)

            # (1-b) 보조표시: 기기군 합산을 구성한 개별 연관키워드 내역.
            members_present = [
                c for c in candidates if c.signal_scores.get("_member_keywords")
            ]
            if members_present:
                st.markdown("**개별 연관키워드 내역 (보조표시 · 합산 구성요소)**")
                pick = st.selectbox(
                    "기기군 선택",
                    [c.category_name for c in members_present],
                    help="합산이 어떤 개별 키워드로 구성됐는지 — 개별은 작아도 합산은 큰지 확인.",
                )
                chosen = next(c for c in members_present if c.category_name == pick)
                members = chosen.signal_scores.get("_member_keywords", [])
                agg = chosen.signal_scores.get("_raw_search_volume") or 0
                st.markdown(
                    f"**{pick}** — 멤버 {len(members)}개, 기기군 합산 **{agg:,}** "
                    f"(개별 최대 {max((m['search_volume'] for m in members), default=0):,})"
                )
                st.dataframe(
                    [
                        {"개별 연관키워드": m["keyword"], "개별 월검색수": m["search_volume"]}
                        for m in members
                    ],
                    use_container_width=True,
                    hide_index=True,
                )

            # (2) 현재 config 경계값(검색량 기준).
            st.markdown("**현재 시장규모 경계 (config, 검색량 기준)**")
            st.dataframe(
                [
                    {"경계": "대 (LARGE_SEARCHVOL)", "현재값": config.MARKET_SIZE_LARGE_SEARCHVOL},
                    {"경계": "중 (MEDIUM_SEARCHVOL)", "현재값": config.MARKET_SIZE_MEDIUM_SEARCHVOL},
                    {"경계": "노출광고 많음 (AD_DEPTH_HIGH)", "현재값": config.AD_DEPTH_HIGH_THRESHOLD},
                    {"경계": "광고싸움 compIdx", "현재값": ", ".join(config.COMP_IDX_AD_WAR_VALUES)},
                ],
                use_container_width=True,
                hide_index=True,
            )

            # (3) 검색량 기준 후보만 모아 분포 vs 경계 비교 + 제안(미적용).
            sv_candidates = [
                c for c in candidates if c.signal_scores.get("_market_size_basis") == "검색량"
            ]
            volumes = [
                float(c.signal_scores.get("_raw_search_volume") or 0) for c in sv_candidates
            ]
            if not volumes:
                st.info("검색량 기준 후보가 없어 경계 점검을 생략합니다(추정매출 기준 소스).")
            else:
                summ = diagnostics.summarize_volumes(volumes)
                cur_large = config.MARKET_SIZE_LARGE_SEARCHVOL
                cur_medium = config.MARKET_SIZE_MEDIUM_SEARCHVOL
                cur_counts = diagnostics.bucket_counts(volumes, cur_large, cur_medium)

                st.markdown("**실제 검색량 분포 (검색량 기준 후보 %d개)**" % summ["n"])
                st.dataframe(
                    [
                        {
                            "최소": round(summ["min"]),
                            "중앙값": round(summ["median"]),
                            "80백분위": round(summ["p80"]),
                            "최대": round(summ["max"]),
                        }
                    ],
                    use_container_width=True,
                    hide_index=True,
                )
                st.markdown(
                    f"현재 경계로 분류: 대 {cur_counts['대']} · 중 {cur_counts['중']} · 소 {cur_counts['소']}"
                )

                # 진단: 최대 검색량이 '대' 경계에 한참 못 미치면 경계가 높다는 신호.
                if summ["max"] < cur_large:
                    st.warning(
                        f"⚠️ 후보 최대 검색량({round(summ['max']):,})이 현재 '대' 경계"
                        f"({cur_large:,})보다 작습니다 → 아무도 '대'에 못 들어가고 신호7이 전반적으로 "
                        f"낮게 눌립니다. 경계가 이 데이터엔 너무 높을 수 있습니다."
                    )

                proposal = diagnostics.propose_search_volume_boundaries(volumes)
                if proposal is None:
                    st.caption("표본이 적어(3개 미만) 데이터 기반 경계 제안은 생략합니다.")
                else:
                    prop_counts = diagnostics.bucket_counts(
                        volumes, proposal["large"], proposal["medium"]
                    )
                    st.markdown("**📐 데이터 기반 경계 제안 (참고용 · 미적용)**")
                    st.dataframe(
                        [
                            {
                                "경계": "대",
                                "현재값": cur_large,
                                "제안값(80백분위)": proposal["large"],
                            },
                            {
                                "경계": "중",
                                "현재값": cur_medium,
                                "제안값(40백분위)": proposal["medium"],
                            },
                        ],
                        use_container_width=True,
                        hide_index=True,
                    )
                    st.markdown(
                        f"제안 경계로 바꾸면 분류: 대 {prop_counts['대']} · 중 {prop_counts['중']} · "
                        f"소 {prop_counts['소']} (현재: 대 {cur_counts['대']} · 중 {cur_counts['중']} · "
                        f"소 {cur_counts['소']})"
                    )
                    st.info(
                        "⚠️ 제안값은 **적용하지 않았습니다**. 검토 후 승인하시면 "
                        "config.py 의 MARKET_SIZE_LARGE/MEDIUM_SEARCHVOL 을 바꿔 적용합니다."
                    )

    st.divider()
    st.caption(
        "스펙 12절 #1(수집 방식)·#2(채널 수수료) 확정 후 stub(MockAdapter)을 "
        "실제 수집 어댑터로 교체하세요. 심리형/비소모품 발굴 경로는 후속 Phase로 보류."
    )


# ───────────────────────────────────────────────────────────────────────────
# 탭 2 — 차종 수요 (Phase C). 단순 표시(등록 대조 없음).
# ───────────────────────────────────────────────────────────────────────────
# ★API 호출 캐싱: 클릭마다 검색광고+데이터랩을 재호출하면 느리고 rate limit 에 걸린다.
#   파이프라인 호출 전체를 1시간 캐시. '새로고침' 버튼으로 수동 무효화(아래 render 참조).
@st.cache_data(ttl=3600, show_spinner="차종 수요 수집 중 (검색광고 + 데이터랩)...")
def _load_car_demand():
    """파이프라인(src.core.car_demand) 호출 → (규모순 rows, 추세 dict, 안내문).

    표 포맷은 하지 않는다 — 파이프라인 결과(ModelRow/Trend)를 그대로 돌려주고
    렌더는 호출부가 st.dataframe 으로 한다(터미널 표 포맷 중복 없음)."""
    idx = load_car_models()
    seeds = config.CAR_PART_SEEDS
    adapter = NaverAdapter([s for ss in seeds.values() for s in ss])  # NAVER_AD_* 키 검증
    agg = harvest_models(adapter, seeds, idx)
    rows = rank_models(agg, idx, config.MODEL_MIN_VOLUME)

    # 추세는 랭킹 모델 '전체'(중복 정규명 1회). 모델 단위로 데이터랩 그룹 묶음.
    canon_kw = model_member_keywords(agg)
    ranked_canons = {r.canonical for r in rows}
    canon_kw = {c: kw for c, kw in canon_kw.items() if c in ranked_canons}

    cid = os.environ.get("NAVER_CLIENT_ID", "").strip()
    csec = os.environ.get("NAVER_CLIENT_SECRET", "").strip()
    if not (cid and csec):
        return rows, {}, ("데이터랩 키(NAVER_CLIENT_ID/SECRET)가 .env 에 없어 추세를 "
                          "수집하지 못했습니다 — 추세 컬럼은 '데이터부족'으로 표시됩니다.")
    trends = car_cli.fetch_trends(canon_kw, cid, csec)  # scripts 의 데이터랩 수집 재사용
    return rows, trends, None


_LIMITS_MD = (
    "**[한계] — 표 해석 주의**\n\n"
    "① 추세는 '이미 검색량이 쌓인' 모델만 잡습니다 — 출시 직후 진짜 신차는 바닥이라 "
    "안 보입니다(그 구간은 시경의 시장감각이 데이터보다 빠름).\n\n"
    "② 데이터랩은 상대값 — 추세 '크기'는 모델 간 직접 비교 불가, '방향' 신호로만.\n\n"
    "③ 저신뢰(멤버1·규모 하한 근처) 모델은 방향만 표시(비율 숨김).\n\n"
    "· 규모(검색광고 절대값)와 추세(데이터랩)는 **별도 컬럼** — 합산/단일 점수 없음. "
    "'(세대미상)' 모호 버킷은 세대 판별이 필요합니다."
)


def _demand_table_rows(rows, trends):
    """ModelRow + Trend → st.dataframe 용 dict 리스트. 추세 셀·신뢰도는 터미널 헬퍼 재사용."""
    table = []
    for r in rows:
        t = trends.get(r.canonical)
        if t is None:
            cell, conf = "데이터부족", "저신뢰"
        else:
            lc = car_cli._low_conf(r, t)
            cell, conf = car_cli._trend_cell(t, lc), ("저신뢰" if lc else "정상")
        table.append(
            {
                "정규명": r.canonical,
                "부품유형": r.part_type,
                "합산검색량": r.volume,
                "멤버": r.members,
                "추세": cell,
                "신뢰도": conf,
                "세대미상": "예" if r.ambiguous else "",
            }
        )
    return table


def render_car_demand() -> None:
    st.title("Phase C — 차종 수요 스캐너")
    st.caption(
        "부품 시드 수확 → 차종 인식 → 모델별 합산 규모(C-3) + 데이터랩 월별 추세(C-4). "
        "규모·추세 별도 컬럼 — 단일 매력도 점수 없음. (단순 표시 — 등록 대조 없음)"
    )

    col_btn, col_note = st.columns([1, 4])
    with col_btn:
        if st.button("🔄 새로고침", help="캐시를 비우고 검색광고+데이터랩을 다시 호출합니다."):
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
        rows, trends, note = _load_car_demand()
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
        column_config={
            # 천단위 콤마 + 숫자 정렬 유지(문자열로 굳히지 않음). 색칠·점수 없음.
            "합산검색량": st.column_config.NumberColumn("합산검색량", format="localized"),
            "멤버": st.column_config.NumberColumn("멤버", format="localized"),
        },
    )


# ───────────────────────────────────────────────────────────────────────────
# 탭 컨테이너
# ───────────────────────────────────────────────────────────────────────────
tab_cat, tab_car = st.tabs(["카테고리 발굴 (Layer 1)", "차종 수요 (Phase C)"])
with tab_cat:
    render_category_discovery()
with tab_car:
    render_car_demand()
