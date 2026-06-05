"""
test_signals.py — 발굴 신호 8개 산출 로직 검증 (스펙 3.2).
"""

import config
from src.schema import CategoryObservation
from src.signals import (
    compute_all_signals,
    detect_ad_war,
    estimate_market_revenue,
    market_size_bucket,
    signal_3_price_gap,
    signal_4_repurchase_cycle,
    signal_5_compat_competition,
    signal_8_winnability,
)


def _obs(**overrides) -> CategoryObservation:
    """필수 필드를 채운 기본 관측치 + 오버라이드."""
    base = dict(
        category_name="t",
        discovery_pattern="호환소모품",
        base_device_bestseller_rank=10,
        base_device_search_volume=100_000,
        has_consumable=True,
        oem_price_krw=20_000,
        compatible_price_krw=8_000,
        repurchase_cycle_days=90,
        compatible_seller_count=10,
        oem_producible=True,
        top_products_review_counts=[1000, 800],
        top_products_avg_price_krw=10_000,
        seller_count=100,
        category_search_volume=50_000,
        naver_ad_cpc_krw=400,
        brand_concentration=0.3,
        lowest_price_sales_share=0.4,
    )
    base.update(overrides)
    return CategoryObservation(**base)


def test_price_gap_larger_gap_scores_higher():
    small_gap = signal_3_price_gap(_obs(oem_price_krw=10_000, compatible_price_krw=9_000))
    big_gap = signal_3_price_gap(_obs(oem_price_krw=10_000, compatible_price_krw=3_000))
    assert big_gap > small_gap


def test_price_gap_missing_data_returns_zero():
    assert signal_3_price_gap(_obs(oem_price_krw=None)) == 0.0
    assert signal_3_price_gap(_obs(compatible_price_krw=None)) == 0.0


def test_repurchase_shorter_cycle_scores_higher():
    short = signal_4_repurchase_cycle(_obs(repurchase_cycle_days=60))
    long = signal_4_repurchase_cycle(_obs(repurchase_cycle_days=600))
    assert short > long


def test_compat_competition_fewer_sellers_scores_higher():
    empty = signal_5_compat_competition(_obs(compatible_seller_count=3))
    saturated = signal_5_compat_competition(_obs(compatible_seller_count=80))
    assert empty > saturated


def test_market_revenue_and_bucket():
    rev = estimate_market_revenue(_obs(top_products_review_counts=[1000], top_products_avg_price_krw=5_000))
    assert rev == 5_000_000
    assert market_size_bucket(config.MARKET_SIZE_LARGE_KRW) == "대"
    assert market_size_bucket(config.MARKET_SIZE_MEDIUM_KRW) == "중"
    assert market_size_bucket(0) == "소"


def test_detect_ad_war_cosmetics_like():
    # 높은 CPC + 브랜드 집중 + 최저가 안 팔림 → 광고 싸움.
    is_ad_war, reasons = detect_ad_war(
        _obs(naver_ad_cpc_krw=2400, brand_concentration=0.8, lowest_price_sales_share=0.05)
    )
    assert is_ad_war is True
    assert len(reasons) >= config.AD_WAR_SIGNAL_COUNT_TO_FLAG


def test_detect_ad_war_value_fight_not_flagged():
    is_ad_war, _ = detect_ad_war(
        _obs(naver_ad_cpc_krw=400, brand_concentration=0.3, lowest_price_sales_share=0.4)
    )
    assert is_ad_war is False


def test_signal_8_lower_when_ad_war():
    war = signal_8_winnability(
        _obs(naver_ad_cpc_krw=2400, brand_concentration=0.8, lowest_price_sales_share=0.05)
    )
    value = signal_8_winnability(
        _obs(naver_ad_cpc_krw=400, brand_concentration=0.3, lowest_price_sales_share=0.4)
    )
    assert war < value


def test_compute_all_signals_keys_present():
    scores = compute_all_signals(_obs())
    for i in range(1, 9):
        assert any(k.startswith(f"signal_{i}_") for k in scores)
    assert "_market_size_bucket" in scores
    assert "_is_ad_war" in scores


def test_all_signal_scores_in_unit_range():
    scores = compute_all_signals(_obs())
    for key, val in scores.items():
        if key.startswith("signal_"):
            assert 0.0 <= val <= 1.0
