"""
test_keyword_intent.py — 구매 의도 분류(buy/mid/info) 정확도·우선순위·정규화·정렬.

분류 규칙(keyword_intent.classify_intent):
  ① 정보형 단어 포함 → 무조건 info(🔴)
  ② 그 외엔 구매형·중간 중 가장 긴 매칭(동률은 구매형) → buy/mid
  ③ 아무것도 안 걸리면 mid(🟡)
"""

from __future__ import annotations

import pytest

from src.core.keyword_intent import (
    BADGE,
    INTENT_RANK,
    annotate_intent,
    classify_intent,
    sort_by_intent,
)


# ── 지시문에 명시된 확정 케이스 ──────────────────────────────────────────────
@pytest.mark.parametrize("kw, expected", [
    ("에어컨필터교체방법", "info"),   # "교체방법" 포함 → 정보형 우선
    ("EV5에어컨필터추천", "buy"),     # "추천"
    ("쏘렌토에어컨필터냄새", "buy"),  # "냄새"(불편)
    ("와이퍼교체주기", "mid"),        # "교체주기"
    ("와이퍼셀프교체", "info"),       # "셀프교체"/"셀프"
    ("와이퍼떨림", "buy"),            # "떨림"(불편)
])
def test_spec_cases(kw, expected):
    assert classify_intent(kw) == expected


# ── 정보형 절대 우선(겹침 함정) ──────────────────────────────────────────────
def test_info_wins_even_with_buy_word():
    # "셀프교체" + "추천" → 정보형 단어가 있으면 무조건 info
    assert classify_intent("와이퍼셀프교체추천") == "info"
    assert classify_intent("에어컨필터청소방법추천") == "info"


def test_neutral_교체_alone_is_not_info():
    # "교체"는 중립어(어느 사전에도 없음) → 단독이면 mid, "방법" 등이 붙어야 info
    assert classify_intent("에어컨필터교체") == "mid"


def test_교체비용_is_buy_not_info():
    # "교체비용"은 "비용"(구매형) 매칭 + 정보형 단어 없음 → 🟢
    assert classify_intent("에어컨필터교체비용") == "buy"
    assert classify_intent("와이퍼교체비용") == "buy"


# ── 가장 긴 매칭(구매형 ↔ 중간 겹침) ────────────────────────────────────────
def test_longest_match_buy_beats_mid():
    # 미세먼지(🟡,4) ⊂ 초미세먼지(🟢,5) → 더 긴 구매형
    assert classify_intent("초미세먼지") == "buy"
    assert classify_intent("에어컨필터초미세먼지") == "buy"


def test_longest_match_mid_beats_buy():
    # 발수(🟢,2) ⊂ 발수코팅(🟡,4) → 더 긴 중간
    assert classify_intent("발수코팅") == "mid"
    assert classify_intent("와이퍼발수코팅") == "mid"


def test_plain_buy_and_mid_words():
    assert classify_intent("와이퍼발수") == "buy"      # 발수만 → 구매형
    assert classify_intent("미세먼지필터") == "mid"    # 미세먼지 → 중간


# ── 정규화: 대소문자·띄어쓰기 무관 ──────────────────────────────────────────
@pytest.mark.parametrize("kw", ["HEPA", "hepa", "Hepa", "헤파필터"])
def test_normalize_hepa(kw):
    assert classify_intent(kw) == "buy"


@pytest.mark.parametrize("kw", ["PM2.5", "pm2.5", "PM 2.5", "pm 2.5"])
def test_normalize_pm25(kw):
    assert classify_intent(kw) == "buy"


def test_normalize_spaces():
    assert classify_intent("와이퍼 셀프 교체") == "info"
    assert classify_intent("에어컨필터 추천") == "buy"


# ── 사전에 없는 키워드 = 기본 중간 ──────────────────────────────────────────
@pytest.mark.parametrize("kw", ["쏘렌토", "EV5", "그랜저신형", ""])
def test_unknown_defaults_to_mid(kw):
    assert classify_intent(kw) == "mid"


# ── 정렬: 🟢 → 🟡 → 🔴, 그룹 내 원래 순서 유지 ──────────────────────────────
def test_sort_by_intent_order_and_stability():
    pairs = [
        ("에어컨필터교체방법", 100),  # info
        ("에어컨필터추천", 90),       # buy
        ("에어컨필터교체주기", 80),   # mid
        ("에어컨필터냄새", 70),       # buy
        ("와이퍼셀프교체", 60),       # info
    ]
    ordered = sort_by_intent(pairs)
    cats = [classify_intent(kw) for kw, _ in ordered]
    assert cats == ["buy", "buy", "mid", "info", "info"]
    # 그룹 내 안정성: 구매형은 입력 순서(추천 → 냄새) 유지
    buys = [kw for kw, _ in ordered if classify_intent(kw) == "buy"]
    assert buys == ["에어컨필터추천", "에어컨필터냄새"]


def test_annotate_intent_preserves_order():
    pairs = [("추천", 5), ("교체방법", 3)]
    assert annotate_intent(pairs) == [("추천", 5, "buy"), ("교체방법", 3, "info")]


def test_badge_and_rank_tables_consistent():
    assert set(BADGE) == set(INTENT_RANK) == {"buy", "mid", "info"}
    assert INTENT_RANK == {"buy": 0, "mid": 1, "info": 2}
