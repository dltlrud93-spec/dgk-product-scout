"""
test_diagnostics.py — 시장규모 경계 점검 보조 함수 검증.

경계값 자체는 바꾸지 않고(승인 전), 분류/분포/제안 계산만 검증한다.
"""

import config
from src import diagnostics
from src.signals import market_size_bucket_by_search_volume


def test_bucket_for_matches_signals_rule():
    # diagnostics.bucket_for 가 signals 의 검색량 버킷 규칙과 동일해야 한다.
    large = config.MARKET_SIZE_LARGE_SEARCHVOL
    medium = config.MARKET_SIZE_MEDIUM_SEARCHVOL
    for v in (0, medium - 1, medium, large - 1, large, large * 3):
        assert diagnostics.bucket_for(v, large, medium) == market_size_bucket_by_search_volume(v)


def test_bucket_counts():
    counts = diagnostics.bucket_counts([5, 25_000, 150_000], large=100_000, medium=20_000)
    assert counts == {"대": 1, "중": 1, "소": 1}


def test_summarize_volumes_basic():
    summ = diagnostics.summarize_volumes([10, 20, 30, 40, 50])
    assert summ["n"] == 5
    assert summ["min"] == 10
    assert summ["max"] == 50
    assert summ["median"] == 30


def test_summarize_empty():
    summ = diagnostics.summarize_volumes([])
    assert summ["n"] == 0
    assert summ["max"] == 0


def test_propose_needs_min_points():
    assert diagnostics.propose_search_volume_boundaries([100, 200]) is None


def test_propose_boundaries_orders_and_rounds():
    vols = [1000, 2000, 3000, 5000, 8000, 12000, 30000, 70000]
    prop = diagnostics.propose_search_volume_boundaries(vols)
    assert prop is not None
    # 중 < 대 보장.
    assert prop["medium"] < prop["large"]
    # 유효숫자 2자리로 깔끔하게 반올림되었는지(끝자리 0이 많은지) 대략 확인.
    assert prop["large"] > 0 and prop["medium"] > 0


def test_propose_reflects_low_volume_distribution():
    # 수만 수준 분포인데 LARGE 경계가 10만이면 아무도 '대'에 못 든다 → 제안은 더 낮아야.
    vols = [3000, 5000, 8000, 12000, 20000, 35000, 60000]
    prop = diagnostics.propose_search_volume_boundaries(vols)
    assert prop["large"] < config.MARKET_SIZE_LARGE_SEARCHVOL
    # 제안 경계로는 적어도 한 개는 '대'로 분류돼야(현재 경계로는 0개).
    cur = diagnostics.bucket_counts(vols, config.MARKET_SIZE_LARGE_SEARCHVOL, config.MARKET_SIZE_MEDIUM_SEARCHVOL)
    prop_counts = diagnostics.bucket_counts(vols, prop["large"], prop["medium"])
    assert cur["대"] == 0
    assert prop_counts["대"] >= 1
