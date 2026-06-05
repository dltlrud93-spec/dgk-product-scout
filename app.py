"""
app.py — Layer 1 카테고리 발굴 랭킹 대시보드 (Streamlit).

실행: streamlit run app.py

현재는 MockAdapter(가짜 데이터) 기반이다. 스펙 12절 #1·#2 확정 후
실제 수집 어댑터로 교체하면 동일 화면이 실데이터로 동작한다.

표시 원칙(스펙 6절 #6): 화면 숫자는 전부 반올림.
"""

from __future__ import annotations

import tempfile

import streamlit as st

try:
    # .env 의 네이버 API 키를 환경변수로 로드(없어도 무시 — 키 검증은 어댑터가 명시 처리).
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import config
from src import diagnostics
from src.adapters.csv_adapter import CSVAdapter
from src.adapters.mock_adapter import MockAdapter
from src.adapters.naver_adapter import NaverAdapter
from src.ranking import discover

st.set_page_config(page_title="dgk-product-scout · Layer 1 카테고리 발굴", layout="wide")

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
            st.stop()
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
            st.stop()

if candidates is None:
    st.stop()  # 아직 입력 대기(예: CSV 미업로드) — 아래 렌더링 생략.

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

    # -----------------------------------------------------------------------
    # 🔍 디버그: 원본값(정규화 전) + 시장규모 경계 점검
    #   정규화 점수만으로는 경계 적절성을 못 보므로 원본 검색량/경쟁정도/노출광고수를
    #   점수와 나란히 보여주고, config 경계 대비 실제 분포를 비교한다.
    # -----------------------------------------------------------------------
    with st.expander("🔍 디버그 — 원본 검색량 & 시장규모 경계 점검", expanded=True):
        # (1) 원본값 ↔ 정규화 점수 나란히.
        st.markdown("**원본값(raw) vs 정규화 점수**")
        debug_rows = []
        for c in candidates:
            s = c.signal_scores
            debug_rows.append(
                {
                    "카테고리": c.category_name,
                    "원본 월검색수(PC+모바일)": s.get("_raw_search_volume"),
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
