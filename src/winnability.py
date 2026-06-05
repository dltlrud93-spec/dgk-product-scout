"""
winnability.py — 진입 가능성 4분면 판정 (스펙 3.3).

세로축 = 시장 크기(대/중/소 → 큼/작음), 가로축 = 가치 싸움 vs 광고 싸움.

| (분면)        | 가치 싸움(이김) | 광고 싸움(짐) |
| 시장 큼       | 최우선          | 함정(필터아웃) |
| 시장 작음     | 틈새            | 무시           |

원칙: 광고 의존도 높음 → 시장이 아무리 커도(좌상단=함정) 제외 대상.
'함정/무시'의 실제 제외(필터) 처리는 ranking.py 가 담당하고,
여기서는 분면 라벨만 판정한다.
"""

from __future__ import annotations


# 시장 규모 버킷(대/중/소) → '큼' 여부. '대','중'을 큰 시장으로 본다.
_LARGE_BUCKETS = {"대", "중"}


def is_large_market(market_size_bucket: str) -> bool:
    """대/중 = 큰 시장, 소 = 작은 시장."""
    return market_size_bucket in _LARGE_BUCKETS


def classify_quadrant(market_size_bucket: str, is_ad_war: bool) -> str:
    """
    시장 크기 버킷 + 광고싸움 여부 → 4분면 라벨.
      최우선 | 틈새 | 함정 | 무시
    """
    large = is_large_market(market_size_bucket)
    if large and not is_ad_war:
        return "최우선"      # 큰 시장 × 가치 싸움
    if not large and not is_ad_war:
        return "틈새"        # 작은 시장 × 가치 싸움
    if large and is_ad_war:
        return "함정"        # 큰 시장 × 광고 싸움 → 필터아웃
    return "무시"            # 작은 시장 × 광고 싸움
