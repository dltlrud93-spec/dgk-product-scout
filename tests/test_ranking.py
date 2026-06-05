"""
test_ranking.py — 카테고리 랭킹/필터 통합 검증 (스펙 3.4 + 보완사항 1).

mock 데이터 기준 검증(스펙 11절 체크리스트):
  - 로봇청소기 소모품·전동칫솔 헤드가 상위에 오는지.
  - 광고 싸움(화장품류 mock)이 신호 8로 필터/하단 처리되는지.
  - 4분면 분류·랭킹 정렬 정확성.
"""

from src.adapters.mock_adapter import MockAdapter
from src.ranking import build_candidate, discover, rank_categories


def _names(candidates):
    return [c.category_name for c in candidates]


def test_robot_and_toothbrush_rank_top():
    # 함정 제외 기본 랭킹에서 두 사례가 상위 2개에 든다.
    ranked = discover(MockAdapter(), include_trap=False)
    top2 = _names(ranked)[:2]
    assert "로봇청소기 호환 소모품" in top2
    assert "전동칫솔 호환 리필 헤드" in top2


def test_cosmetics_filtered_out_by_default():
    # 화장품(스킨케어)은 광고 싸움(함정) → 기본 랭킹에서 제외.
    ranked = discover(MockAdapter(), include_trap=False)
    assert "스킨케어 화장품" not in _names(ranked)


def test_cosmetics_is_ad_war_and_trap_quadrant():
    # 직접 빌드해 4분면/광고싸움 플래그 확인.
    obs = next(
        o for o in MockAdapter().fetch_category_observations()
        if o.category_name == "스킨케어 화장품"
    )
    c = build_candidate(obs)
    assert c.is_ad_war is True
    assert c.winnability == "함정"


def test_cosmetics_visible_but_bottom_when_trap_included():
    # '함정 포함 보기' 토글 시 다시 보이되, 최하단(함정/무시)에 위치.
    ranked = discover(MockAdapter(), include_trap=True)
    names = _names(ranked)
    assert "스킨케어 화장품" in names
    # 함정/무시 분면은 최우선/틈새보다 아래.
    cosmetics_idx = names.index("스킨케어 화장품")
    robot_idx = names.index("로봇청소기 호환 소모품")
    assert cosmetics_idx > robot_idx


def test_trap_not_above_priority_even_with_big_market():
    # 큰 시장(함정)이 감점을 뚫고 상위로 오지 못한다(보완사항 1).
    ranked = discover(MockAdapter(), include_trap=True)
    # 첫 번째는 반드시 '최우선' 분면이어야 한다.
    assert ranked[0].winnability == "최우선"


def test_sort_order_priority_then_score():
    # 4분면 랭크가 1차 키. 같은 분면 내에서는 ranking_score 내림차순.
    ranked = discover(MockAdapter(), include_trap=True)
    from config import QUADRANT_RANK
    prev = (-1, float("inf"))
    for c in ranked:
        key = (QUADRANT_RANK[c.winnability], -c.ranking_score)
        assert key >= prev
        prev = key


def test_include_trap_returns_more_or_equal():
    excluded = discover(MockAdapter(), include_trap=False)
    included = discover(MockAdapter(), include_trap=True)
    assert len(included) >= len(excluded)


def test_rank_categories_empty_input():
    assert rank_categories([], include_trap=False) == []
