"""
test_winnability.py — 4분면 분류 정확성 검증 (스펙 3.3).
"""

from src.winnability import classify_quadrant, is_large_market


def test_large_market_buckets():
    assert is_large_market("대") is True
    assert is_large_market("중") is True
    assert is_large_market("소") is False


def test_quadrant_top_priority():
    # 큰 시장 × 가치 싸움 → 최우선.
    assert classify_quadrant("대", is_ad_war=False) == "최우선"


def test_quadrant_niche():
    # 작은 시장 × 가치 싸움 → 틈새.
    assert classify_quadrant("소", is_ad_war=False) == "틈새"


def test_quadrant_trap():
    # 큰 시장 × 광고 싸움 → 함정.
    assert classify_quadrant("대", is_ad_war=True) == "함정"


def test_quadrant_ignore():
    # 작은 시장 × 광고 싸움 → 무시.
    assert classify_quadrant("소", is_ad_war=True) == "무시"


def test_all_four_quadrants_covered():
    labels = {
        classify_quadrant("대", False),
        classify_quadrant("소", False),
        classify_quadrant("대", True),
        classify_quadrant("소", True),
    }
    assert labels == {"최우선", "틈새", "함정", "무시"}
