"""
ranking.py — 카테고리 후보 생성 + 랭킹 + 함정 필터 (스펙 3.4).

흐름:
  관측치(CategoryObservation)
    → 신호 8개 산출(signals)
    → 4분면 판정(winnability)
    → CategoryCandidate 빌드
    → 랭킹 정렬(큰 시장 × 가치 싸움 우선)
    → 함정/무시 기본 제외 필터(사용자 보완사항 1)

정렬 키:
  1차: 4분면 랭크(최우선 < 틈새 < 함정 < 무시)  ── config.QUADRANT_RANK
  2차: 가중 신호 합산 점수(ranking_score) 내림차순

함정 처리(보완사항 1):
  '함정'(광고 싸움)은 점수 감점이 아니라 기본 제외(필터)다.
  큰 시장이 가중점수로 상위를 뚫지 못하도록, 랭킹에서 빼버린다.
  단, include_trap=True 를 주면(=UI '함정 포함 보기' 토글) 다시 포함한다.
"""

from __future__ import annotations

import config
from src.adapters.base import DataAdapter
from src.schema import CategoryCandidate, CategoryObservation
from src.signals import compute_all_signals
from src.winnability import classify_quadrant


def _ranking_score(signal_scores: dict) -> float:
    """가중 신호 합산 점수(2차 정렬 키). config.RANKING_WEIGHTS 사용."""
    total = 0.0
    for key, weight in config.RANKING_WEIGHTS.items():
        total += signal_scores.get(key, 0.0) * weight
    return total


def _why_opportunity(obs: CategoryObservation, scores: dict, quadrant: str) -> str:
    """'왜 기회인가' 한 줄 설명 생성."""
    if scores["_is_ad_war"]:
        reasons = ", ".join(scores["_ad_war_reasons"])
        return f"광고 싸움({reasons}) — 가성비로 이기기 어려움"
    bits = []
    if scores["signal_3_price_gap"] >= 0.5:
        bits.append("정품 대비 큰 가격 갭")
    if scores["signal_4_repurchase_cycle"] >= 0.5:
        bits.append("짧은 재구매 주기(회전)")
    if scores["signal_7_market_size"] >= 0.5:
        bits.append(f"{scores['_market_size_bucket']} 시장")
    if scores["signal_5_compat_competition"] >= 0.5:
        bits.append("호환 경쟁 공백")
    return " · ".join(bits) if bits else "신호 약함(보류 검토)"


def _entry_difficulty(scores: dict) -> str:
    """진입 난이도 한 줄(광고싸움/경쟁 기반)."""
    if scores["_is_ad_war"]:
        return "높음(광고·브랜드 싸움)"
    w = scores["signal_8_winnability"]
    if w >= 0.8:
        return "낮음(가성비 우위)"
    if w >= 0.5:
        return "중간"
    return "높음"


def build_candidate(obs: CategoryObservation) -> CategoryCandidate:
    """관측치 1건 → 신호 산출 → 4분면 판정 → CategoryCandidate."""
    scores = compute_all_signals(obs)
    quadrant = classify_quadrant(scores["_market_size_bucket"], scores["_is_ad_war"])
    return CategoryCandidate(
        category_name=obs.category_name,
        discovery_pattern=obs.discovery_pattern,
        signal_scores=scores,
        market_size_est=scores["_market_size_bucket"],
        winnability=quadrant,
        why_opportunity=_why_opportunity(obs, scores, quadrant),
        entry_difficulty=_entry_difficulty(scores),
        ranking_score=_ranking_score(scores),
        is_ad_war=scores["_is_ad_war"],
    )


def _sort_key(c: CategoryCandidate) -> tuple:
    """1차 4분면 랭크 오름차순, 2차 ranking_score 내림차순."""
    quadrant_rank = config.QUADRANT_RANK.get(c.winnability, 99)
    return (quadrant_rank, -c.ranking_score)


def rank_categories(
    candidates: list[CategoryCandidate],
    include_trap: bool = False,
) -> list[CategoryCandidate]:
    """
    후보 리스트를 랭킹 정렬하고, 기본적으로 함정/무시를 제외한다.

    include_trap=False (기본): '함정'/'무시' 분면을 제외(필터).
      → 큰 시장이라도 광고 싸움이면 상위로 못 온다(보완사항 1).
    include_trap=True ('함정 포함 보기' 토글): 전부 포함하되 정렬은 동일.
    """
    if include_trap:
        kept = list(candidates)
    else:
        excluded = set()
        if config.EXCLUDE_TRAP_BY_DEFAULT:
            excluded.add("함정")
        if config.EXCLUDE_IGNORE_BY_DEFAULT:
            excluded.add("무시")
        kept = [c for c in candidates if c.winnability not in excluded]
    return sorted(kept, key=_sort_key)


def discover(adapter: DataAdapter, include_trap: bool = False) -> list[CategoryCandidate]:
    """
    어댑터에서 관측치 수집 → 후보 빌드 → 랭킹.
    Layer 1 발굴 엔진의 최상위 진입점.
    """
    observations = adapter.fetch_category_observations()
    candidates = [build_candidate(o) for o in observations]
    return rank_categories(candidates, include_trap=include_trap)
