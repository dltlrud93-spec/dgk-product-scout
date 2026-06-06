"""
search_volume.py — 공통 '검색량 + per-seed 기기군 합산' 코어 (Phase A).

배경: 이 합산 로직은 원래 naver_adapter.py 안(_make_device_group / fetch_category_observations,
커밋 eacc698)에 있었다. 계절 캘린더와 (후속) 차종 수요 스캐너가 *같은 합산*을 쓰도록
'이동·정리'한 것 — 새 로직 없음. HMAC 서명과 검색광고 키워드도구 호출 규약은
naver_adapter 에 그대로 두고(변경 금지), 여기서는 그 응답(keywordList)을 받아 합산만 한다.

합산 규칙(eacc698 그대로):
  · 시드당 1회 호출(멀티시드 배치 폐기 — 배치 응답은 시드별 귀속 불가).
  · 시드 응답의 연관어를 relKeyword 단위 dedupe(중복 부풀림 방지).
  · 소모품 사전(_is_consumable) 통과 연관어 전부 = 그 시드의 '기기군 멤버'(cross-named 포함).
  · 합산 규모 = Σ(멤버 monthlyPcQcCnt + monthlyMobileQcCnt). '< 10' → 0(보수, _parse_volume).
  · 소모품 미통과 연관어 = '시드에 귀속 안 된 자체 후보'(unattributed_candidates).
"""

from __future__ import annotations

import statistics
from typing import Callable, Optional

# 소모품 판정/파싱/경쟁정도 집계는 CSVAdapter 의 검증된 로직을 그대로 재사용(단일 출처).
from src.adapters.csv_adapter import _aggregate_comp_idx, _is_consumable, _parse_volume

# 검색광고 키워드도구 응답 필드명(공식 RelKwdStat). naver_adapter 와 동일 값.
FIELD_REL_KEYWORD = "relKeyword"
FIELD_MONTHLY_PC = "monthlyPcQcCnt"
FIELD_MONTHLY_MOBILE = "monthlyMobileQcCnt"
FIELD_AD_DEPTH = "plAvgDepth"
FIELD_COMP_IDX = "compIdx"

# A-3: 동일 시드 재호출 방지용 프로세스 내 메모이즈(같은 실행에서 중복 호출 차단).
# 교차 실행 영속 캐시(TTL)는 시드가 많아질 때 도입 — 현재 규모(수십 시드)에선 불필요.
# [확인 필요] 영속 캐시 TTL 도입 시 config.SEARCH_VOLUME_CACHE_TTL_SECONDS 로 노출.
_SEED_CACHE: dict[str, dict] = {}


def member_volume(row: dict) -> int:
    """한 연관어 행의 규모 = monthlyPcQcCnt + monthlyMobileQcCnt. '< 10'·결측 → 0(보수)."""
    return int(
        _parse_volume(row.get(FIELD_MONTHLY_PC))
        + _parse_volume(row.get(FIELD_MONTHLY_MOBILE))
    )


def dedupe_relkeywords(rows: list[dict]) -> dict[str, dict]:
    """relKeyword 단위 dedupe(첫 출현만 유지). 시드 응답 내 중복 합산 부풀림 방지."""
    seen: dict[str, dict] = {}
    for row in rows:
        rel = str(row.get(FIELD_REL_KEYWORD) or "").strip()
        if rel and rel not in seen:
            seen[rel] = row
    return seen


def aggregate_seed(rows: list[dict]) -> dict:
    """
    한 시드의 keywordList 응답 → 기기군 합산 결과(dict).

    반환:
      total_volume: int                         # 소모품 멤버 규모 합
      member_keywords: list[{relKeyword, volume, compIdx, plAvgDepth}]   # 규모 내림차순
      unattributed_candidates: list[{...}]       # 소모품 미통과 연관어(자체 후보), 규모 내림차순
      comp_idx: Optional[str]                    # 멤버 대표 경쟁정도(서수평균)
      avg_ad_depth: Optional[float]              # 멤버 평균 노출광고수
    """
    uniq = dedupe_relkeywords(rows)
    members: list[dict] = []
    unattributed: list[dict] = []
    for rel, row in uniq.items():
        rec = {
            "relKeyword": rel,
            "volume": member_volume(row),
            "compIdx": row.get(FIELD_COMP_IDX),
            "plAvgDepth": row.get(FIELD_AD_DEPTH),
        }
        (members if _is_consumable(rel) else unattributed).append(rec)

    members.sort(key=lambda m: m["volume"], reverse=True)
    unattributed.sort(key=lambda m: m["volume"], reverse=True)

    total_volume = sum(m["volume"] for m in members)
    comp_idx = _aggregate_comp_idx([str(m["compIdx"] or "") for m in members])
    depths = [_parse_volume(m["plAvgDepth"]) for m in members]
    avg_ad_depth = statistics.mean(depths) if depths else None

    return {
        "total_volume": total_volume,
        "member_keywords": members,
        "unattributed_candidates": unattributed,
        "comp_idx": comp_idx,
        "avg_ad_depth": avg_ad_depth,
    }


def fetch_aggregated_volume(
    seed_keywords: list[str],
    *,
    adapter=None,
    request_fn: Optional[Callable[[str], list[dict]]] = None,
    use_cache: bool = True,
) -> dict:
    """
    시드별 기기군 합산을 한 번에 수집한다. 시드당 1회 호출(eacc698 핵심).

    호출 경로(둘 중 하나):
      · request_fn(seed)->keywordList 를 주면 그걸로 호출(테스트/대체용). rate limit 은 호출자 책임.
      · 아니면 NaverAdapter 를 만들어(또는 주입된 adapter) _request_keywordstool 로 호출.
        서명/헤더/429 백오프/호출 간 rate limit sleep 은 naver_adapter 규약 그대로 사용.

    반환: { seed: aggregate_seed(...) }
    """
    seeds = [s.strip() for s in seed_keywords if s and s.strip()]

    if request_fn is not None:
        return {seed: aggregate_seed(request_fn(seed)) for seed in seeds}

    if adapter is None:
        from src.adapters.naver_adapter import NaverAdapter  # 지연 import(순환 방지)

        adapter = NaverAdapter(seeds)

    out: dict[str, dict] = {}
    did_call = False  # 첫 실제 호출 전에는 sleep 하지 않음(원 어댑터 동작과 동일).
    for seed in seeds:
        if use_cache and seed in _SEED_CACHE:
            out[seed] = _SEED_CACHE[seed]
            continue
        if did_call:
            adapter._sleep(adapter.rate_limit_seconds)  # 호출 간 rate limit
        agg = aggregate_seed(adapter._request_keywordstool([seed]))
        did_call = True
        out[seed] = agg
        if use_cache:
            _SEED_CACHE[seed] = agg
    return out


def clear_cache() -> None:
    """프로세스 내 시드 캐시 비우기(테스트/재측정용)."""
    _SEED_CACHE.clear()
