"""
diagnostics.py — 시장규모 경계(신호 7) 점검용 순수 함수 모음.

대시보드가 0~1 정규화 점수만 보여줘 원본 검색량과 경계 적절성이 안 보이는 문제를
디버깅하기 위한 보조 로직. 여기서는 '계산'만 하고 config 경계값은 절대 바꾸지 않는다
(경계 조정은 사용자 승인 후 config.py 에서만).

핵심 제공:
  - bucket_for / bucket_counts : 주어진 경계로 검색량을 대/중/소로 분류.
  - summarize_volumes          : 후보들의 원본 검색량 분포(min/median/max/백분위).
  - propose_search_volume_boundaries : 실제 분포 기반 경계 '제안값'(미적용, 참고용).
"""

from __future__ import annotations

from math import floor, log10
from typing import Optional


def bucket_for(volume: float, large: float, medium: float) -> str:
    """검색량 → 대/중/소 (signals.market_size_bucket_by_search_volume 과 동일 규칙)."""
    if volume >= large:
        return "대"
    if volume >= medium:
        return "중"
    return "소"


def bucket_counts(volumes: list[float], large: float, medium: float) -> dict:
    """검색량 리스트를 주어진 경계로 분류한 개수(대/중/소)."""
    counts = {"대": 0, "중": 0, "소": 0}
    for v in volumes:
        counts[bucket_for(v, large, medium)] += 1
    return counts


def _percentile(sorted_vals: list[float], p: float) -> float:
    """선형보간 백분위(p=0.0~1.0). 빈 리스트는 0.0."""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sorted_vals[0])
    k = (n - 1) * p
    f = floor(k)
    c = min(f + 1, n - 1)
    if f == c:
        return float(sorted_vals[int(k)])
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def _round_sig(x: float, sig: int = 2) -> int:
    """양수 x 를 유효숫자 sig 자리로 반올림한 정수(경계 제안값을 '깔끔한 수'로)."""
    if x <= 0:
        return 0
    digits = sig - int(floor(log10(x))) - 1
    return int(round(x, digits))


def summarize_volumes(volumes: list[float]) -> dict:
    """원본 검색량 분포 요약(min/median/max + 80/40 백분위). 빈 입력은 0 채움."""
    vals = sorted(float(v) for v in volumes)
    if not vals:
        return {"n": 0, "min": 0, "median": 0, "max": 0, "p80": 0, "p40": 0}
    return {
        "n": len(vals),
        "min": vals[0],
        "median": _percentile(vals, 0.5),
        "max": vals[-1],
        "p80": _percentile(vals, 0.80),
        "p40": _percentile(vals, 0.40),
    }


def propose_search_volume_boundaries(
    volumes: list[float], min_points: int = 3
) -> Optional[dict]:
    """
    실제 후보 검색량 분포에서 시장규모 경계 '제안값'을 만든다(미적용, 참고용).

    근거(설명 가능해야 하므로 단순 규칙):
      - 대 경계 ≈ 80 백분위(상위 20%만 '대'), 중 경계 ≈ 40 백분위.
      - 깔끔한 수가 되도록 유효숫자 2자리로 반올림.
      - 중 < 대가 보장되도록 보정.
    표본이 min_points 미만이면 제안하지 않는다(과적합 방지) → None.
    """
    vals = sorted(float(v) for v in volumes)
    if len(vals) < min_points:
        return None
    large = _round_sig(_percentile(vals, 0.80), 2)
    medium = _round_sig(_percentile(vals, 0.40), 2)
    if medium <= 0:
        medium = max(1, _round_sig(_percentile(vals, 0.50), 1))
    if large <= medium:
        large = medium * 2  # 경계 역전 방지(보수적 분리)
    return {"large": large, "medium": medium}
