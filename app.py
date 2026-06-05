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

from src.adapters.csv_adapter import CSVAdapter
from src.adapters.mock_adapter import MockAdapter
from src.ranking import discover

st.set_page_config(page_title="dgk-product-scout · Layer 1 카테고리 발굴", layout="wide")

st.title("Layer 1 — 카테고리 발굴 랭킹")
st.caption(
    "어떤 제품군에 진입할까? · 큰 시장 × 가치 싸움 우선 · 광고 싸움(함정)은 기본 제외"
)

# --- 데이터 소스 선택 ---
source = st.sidebar.radio(
    "데이터 소스",
    ["Mock(테스트)", "CSV(오빠두 연관검색어)"],
    help="CSV = 네이버 검색광고 연관검색어 스크랩 엑셀에서 내보낸 실데이터.",
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

st.divider()
st.caption(
    "스펙 12절 #1(수집 방식)·#2(채널 수수료) 확정 후 stub(MockAdapter)을 "
    "실제 수집 어댑터로 교체하세요. 심리형/비소모품 발굴 경로는 후속 Phase로 보류."
)
