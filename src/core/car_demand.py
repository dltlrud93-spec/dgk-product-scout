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
                               {"volume": 0, "members": 0, "ambiguous": r.ambiguous})
            a["volume"] += vol
            a["members"] += 1
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
