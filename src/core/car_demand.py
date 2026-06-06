"""
car_demand.py — Phase C-3 차종 수요 규모 파이프라인 (본 파이프라인).

부품 시드 수확 → 차종 인식(car_models) → (정규명 × 부품유형) 합산 → 규모 랭킹.
합산 코어(search_volume)의 dedupe/member_volume/호출 경로를 재사용한다(코어 무변경).
소모품 필터(aggregate_seed)는 쓰지 않는다 — Phase C 는 '차종 인식'으로 묶는다.

규모와 추세는 별도다(C-4 추세는 별도 컬럼). 여기서는 규모만. 단일 매력도 점수 없음.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.core.search_volume import dedupe_relkeywords, member_volume


@dataclass
class ModelRow:
    canonical: str       # 정규명 (모호 버킷이면 "{family}(세대미상)")
    part_type: str       # 부품유형 (에어컨필터 / 와이퍼)
    volume: int          # 합산검색량 (그 모델+부품유형으로 인식된 연관어들의 단일 검색량 합)
    members: int         # 합산에 들어간 연관어 수
    ambiguous: bool      # 세대미상 모호 버킷이면 True
    maker: str


def harvest_models(adapter, part_seeds: dict[str, list[str]], index) -> dict:
    """
    부품 시드 수확 → 차종 인식 → (정규명, 부품유형)별 합산.

    같은 부품유형 내 시드 교차 dedup(rel 1회만, 합산 부풀림 방지). 시드당 1회 호출.
    반환: {(canonical, part_type): {"volume", "members", "ambiguous"}}.
    """
    flat = [(seed, ptype) for ptype, seeds in part_seeds.items() for seed in seeds]
    per_type: dict[str, dict[str, int]] = {ptype: {} for ptype in part_seeds}
    for i, (seed, ptype) in enumerate(flat):
        if i > 0:
            adapter._sleep(adapter.rate_limit_seconds)   # 호출 간 rate limit
        uniq = dedupe_relkeywords(adapter._request_keywordstool([seed]))
        for rel, row in uniq.items():
            per_type[ptype].setdefault(rel, member_volume(row))

    agg: dict[tuple, dict] = {}
    for ptype, kwmap in per_type.items():
        for rel, vol in kwmap.items():
            r = index.recognize(rel)
            if not r.recognized:
                continue
            a = agg.setdefault((r.canonical, ptype),
                               {"volume": 0, "members": 0, "ambiguous": r.ambiguous,
                                "keywords": []})
            a["volume"] += vol
            a["members"] += 1
            a["keywords"].append((rel, vol))   # 추세(C-4)에서 모델 그룹 키워드로 재사용
    return agg


def rank_models(agg: dict, index, min_volume: int | None) -> list[ModelRow]:
    """합산 결과 → ModelRow 규모순 리스트. min_volume 미만은 컷(None 이면 컷 없음)."""
    rows = [
        ModelRow(canonical=canon, part_type=ptype, volume=a["volume"], members=a["members"],
                 ambiguous=a["ambiguous"], maker=index.maker_of.get(canon, ""))
        for (canon, ptype), a in agg.items()
    ]
    if min_volume is not None:
        rows = [r for r in rows if r.volume >= min_volume]
    rows.sort(key=lambda r: r.volume, reverse=True)
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# C-4 추세 (데이터랩 상대값. 규모와 별도 — 합산/단일점수 금지)
# ─────────────────────────────────────────────────────────────────────────────

# 데이터랩 공식 한도(추측 아님): 요청당 키워드그룹 ≤5, 그룹당 키워드 ≤20.
DATALAB_MAX_GROUPS_PER_REQUEST = 5
DATALAB_MAX_KEYWORDS_PER_GROUP = 20


@dataclass
class Trend:
    ratio: float | None    # 상승률(최근÷baseline). None = 신규후보/데이터부족(비율 계산 안 함)
    direction: str         # "↑" / "↓" / "보합" / "신규 후보" / "데이터부족"
    new_candidate: bool     # baseline≈0 & 최근 신호 = 떠오르는 신차 후보
    recent_avg: float
    baseline_avg: float
    data_insufficient: bool  # 시계열이 너무 짧음 → 저신뢰


def model_member_keywords(agg: dict) -> dict[str, list[tuple[str, int]]]:
    """(정규명×부품유형) 합산 → 모델(정규명) 단위로 멤버 키워드 통합. 부품유형 합쳐 한 그룹.

    같은 rel 이 여러 부품유형에 있으면 최대 규모로 dedup. 규모 내림차순 정렬(그룹 채울 때 상위부터).
    """
    merged: dict[str, dict[str, int]] = {}
    for (canon, _ptype), a in agg.items():
        best = merged.setdefault(canon, {})
        for rel, vol in a["keywords"]:
            best[rel] = max(best.get(rel, 0), vol)
    return {canon: sorted(b.items(), key=lambda x: x[1], reverse=True) for canon, b in merged.items()}


def select_group_keywords(member_keywords: list[tuple[str, int]],
                          limit: int = DATALAB_MAX_KEYWORDS_PER_GROUP) -> list[str]:
    """데이터랩 한 그룹에 넣을 키워드 — 규모 상위부터 limit 개."""
    return [rel for rel, _ in member_keywords[:limit]]


def compute_trend(periods: list[str], ratios: list[float], *, recent_months: int,
                  baseline_months: int, near_zero: float, up: float, down: float) -> Trend:
    """
    모델의 월별 ratio 시계열 → 추세. 상승률 = 최근 recent_months 평균 ÷ 직전 baseline_months 평균.

    · baseline 평균 ≤ near_zero 면 비율 계산 금지 → 최근에 신호 있으면 '신규 후보', 없으면 '데이터부족'.
    · 시계열이 recent+1 보다 짧으면 '데이터부족'(저신뢰).
    """
    pts = sorted(zip(periods, ratios))
    vals = [r for _, r in pts]
    if len(vals) < recent_months + 1:
        return Trend(None, "데이터부족", False, 0.0, 0.0, True)
    recent = vals[-recent_months:]
    baseline = vals[-(recent_months + baseline_months):-recent_months] or vals[:-recent_months]
    recent_avg = sum(recent) / len(recent)
    baseline_avg = (sum(baseline) / len(baseline)) if baseline else 0.0

    if baseline_avg <= near_zero:
        if recent_avg > near_zero:
            return Trend(None, "신규 후보", True, recent_avg, baseline_avg, False)
        return Trend(None, "데이터부족", False, recent_avg, baseline_avg, True)

    ratio = recent_avg / baseline_avg
    direction = "↑" if ratio >= up else ("↓" if ratio <= down else "보합")
    return Trend(ratio, direction, False, recent_avg, baseline_avg, False)
