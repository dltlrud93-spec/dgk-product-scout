"""
schema.py — Layer 1 데이터 스키마.

스펙 3.4 의 category_candidate 레코드 + 어댑터가 반환하는 카테고리
원시 관측치(CategoryObservation)를 정의한다.

원시 관측치(외부 수집 결과)와 산출 결과(category_candidate)를 분리한다:
  - CategoryObservation : 어댑터가 외부 소스에서 가져오는 '날것'의 값.
  - CategoryCandidate   : 신호 8개 산출 + 4분면 판정을 거친 최종 출력(3.4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CategoryObservation:
    """
    어댑터가 외부 소스(마켓/데이터랩/1688/네이버 검색광고 등)에서 가져오는
    카테고리 단위 원시 관측치.

    P1 에서 실제 수집부는 미확정(스펙 12절 #1)이므로, 이 구조는
    '수집부가 채워줘야 하는 계약'이다. MockAdapter 가 가짜 값으로 채운다.

    값을 알 수 없는 항목은 None 으로 두며, 신호 산출 시 보수적으로 처리한다.
    """

    category_name: str                          # 예: "로봇청소기 호환 소모품"
    discovery_pattern: str                       # "호환소모품" | "심리형" 등(3.1)

    # --- 신호 1: 베이스 기기 인기/보유 규모 ---
    base_device_bestseller_rank: Optional[int]   # 기기 베스트셀러 순위(작을수록 인기)
    base_device_search_volume: Optional[int]     # 기기 월 검색량(데이터랩)

    # --- 신호 2: 소모품 존재 ---
    has_consumable: bool                         # 정기 교체 부품 존재 여부

    # --- 신호 3: 정품 가격 갭 ---
    oem_price_krw: Optional[float]               # 정품 소모품가
    compatible_price_krw: Optional[float]        # 호환 가능가(1688 등 기반 추정)

    # --- 신호 4: 재구매 주기 ---
    repurchase_cycle_days: Optional[int]         # 평균 교체 주기(일)

    # --- 신호 5: 호환 경쟁 강도 ---
    compatible_seller_count: Optional[int]       # 호환 소모품 셀러 수(포화/공백)

    # --- 신호 6: 우리 역량 적합 (중국 OEM 생산 가능 등) ---
    oem_producible: bool                         # 중국 OEM 생산 가능 여부

    # --- 신호 7: 시장 규모/수요 (추정) ---
    top_products_review_counts: list[int] = field(default_factory=list)  # 상위 상품 리뷰수들
    top_products_avg_price_krw: Optional[float] = None                   # 상위 상품 평균가
    seller_count: Optional[int] = None                                   # 판매자 수
    category_search_volume: Optional[int] = None                        # 카테고리 검색량

    # --- 신호 8: 진입 가능성 / 광고 의존도 ---
    naver_ad_cpc_krw: Optional[float] = None        # 네이버 검색광고 평균 CPC
    brand_concentration: Optional[float] = None     # 상위 매출의 브랜드 집중도(0~1)
    lowest_price_sales_share: Optional[float] = None  # 최저가 상품 매출 점유(0~1)
    # 검색광고 키워드도구/CSV 소스 전용(마켓 소스에선 None):
    comp_idx: Optional[str] = None                  # 경쟁정도(compIdx): "낮음" | "중간" | "높음"
    avg_ad_depth: Optional[float] = None            # 월평균노출광고수(plAvgDepth)


@dataclass
class CategoryCandidate:
    """
    스펙 3.4 — Layer 1 출력 레코드.

    원 스펙은 dict 예시지만, 오타/누락 방지를 위해 dataclass 로 구현하고
    to_dict() 로 스펙과 동일한 키 형태를 제공한다.
    """

    category_name: str          # 예: "공기청정기 호환 필터"
    discovery_pattern: str      # 호환소모품 | 심리형 | ...
    signal_scores: dict         # 신호 1~8 점수(0.0~1.0) 및 부가 메타
    market_size_est: str        # 대 | 중 | 소 (추정)
    winnability: str            # 4분면 위치: 최우선 | 틈새 | 함정 | 무시
    why_opportunity: str        # "왜 기회인가" 한 줄
    entry_difficulty: str       # 예상 진입 난이도

    # 랭킹/필터에 쓰는 파생값(스펙 외 보조 필드).
    ranking_score: float = 0.0  # 가중 신호 합산 점수(2차 정렬 키)
    is_ad_war: bool = False     # 광고 싸움 여부(함정/무시 필터 근거)

    def to_dict(self) -> dict:
        """스펙 3.4 와 동일한 키 구조의 dict 로 반환."""
        return {
            "category_name": self.category_name,
            "discovery_pattern": self.discovery_pattern,
            "signal_scores": self.signal_scores,
            "market_size_est": self.market_size_est,
            "winnability": self.winnability,
            "why_opportunity": self.why_opportunity,
            "entry_difficulty": self.entry_difficulty,
        }
