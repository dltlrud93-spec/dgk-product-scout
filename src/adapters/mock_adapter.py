"""
mock_adapter.py — DataAdapter 의 mock 구현 (stub).

⚠️ 여기의 모든 숫자는 '개발/테스트용으로 명시적으로 지어낸 가짜 값'이다.
실제 시장 데이터가 아니다. 스펙 12절 #1·#2 가 확정되면 실제 수집 어댑터로 교체한다.

mock 데이터 설계 의도(스펙 11절 테스트 체크리스트 재현):
  - 로봇청소기 소모품 / 전동칫솔 헤드 : 알려진 좋은 사례 → 상위로 와야 함.
  - 화장품류 : 광고 싸움(높은 CPC·브랜드 집중·최저가 안 팔림) → 신호 8로 필터/하단.
  - 그 외 가성비/틈새/포화 사례를 섞어 4분면·랭킹 로직을 검증.
"""

from __future__ import annotations

from src.adapters.base import DataAdapter
from src.schema import CategoryObservation


class MockAdapter(DataAdapter):
    """고정된 mock 카테고리 관측치를 반환하는 stub 어댑터."""

    def fetch_category_observations(self) -> list[CategoryObservation]:
        return [
            # --- 로봇청소기 호환 소모품: 큰 시장 × 가치 싸움 → '최우선' 기대 ---
            CategoryObservation(
                category_name="로봇청소기 호환 소모품",
                discovery_pattern="호환소모품",
                base_device_bestseller_rank=3,
                base_device_search_volume=180_000,
                has_consumable=True,
                oem_price_krw=29_000,            # 정품 소모품가
                compatible_price_krw=9_900,      # 호환가(갭 큼 → 기회)
                repurchase_cycle_days=60,        # 짧은 교체 주기 → 회전
                compatible_seller_count=12,
                oem_producible=True,
                top_products_review_counts=[8200, 5400, 4100, 3000, 2600],
                top_products_avg_price_krw=15_000,
                seller_count=140,
                category_search_volume=90_000,
                naver_ad_cpc_krw=420,            # 낮은 CPC → 가성비 통함
                brand_concentration=0.30,
                lowest_price_sales_share=0.42,   # 최저가가 잘 팔림 → 가치 싸움
            ),
            # --- 전동칫솔 리필 헤드: 큰 시장 × 가치 싸움 → '최우선' 기대 ---
            CategoryObservation(
                category_name="전동칫솔 호환 리필 헤드",
                discovery_pattern="호환소모품",
                base_device_bestseller_rank=5,
                base_device_search_volume=150_000,
                has_consumable=True,
                oem_price_krw=24_000,
                compatible_price_krw=7_500,      # 갭 큼
                repurchase_cycle_days=90,        # 분기 교체 → 회전
                compatible_seller_count=18,
                oem_producible=True,
                top_products_review_counts=[6100, 4800, 3300, 2200, 1900],
                top_products_avg_price_krw=12_000,
                seller_count=110,
                category_search_volume=70_000,
                naver_ad_cpc_krw=510,
                brand_concentration=0.35,
                lowest_price_sales_share=0.38,
            ),
            # --- 화장품(스킨케어): 큰 시장 × 광고 싸움 → '함정' 기대(신호 8 필터) ---
            CategoryObservation(
                category_name="스킨케어 화장품",
                discovery_pattern="비소모품",
                base_device_bestseller_rank=None,  # 베이스 기기 개념 없음
                base_device_search_volume=None,
                has_consumable=False,
                oem_price_krw=None,
                compatible_price_krw=None,
                repurchase_cycle_days=120,
                compatible_seller_count=None,
                oem_producible=True,
                top_products_review_counts=[42000, 31000, 28000, 25000, 20000],
                top_products_avg_price_krw=38_000,
                seller_count=900,
                category_search_volume=500_000,    # 시장 큼
                naver_ad_cpc_krw=2_400,            # 매우 높은 CPC → 광고싸움
                brand_concentration=0.78,         # 브랜드 쏠림 심함
                lowest_price_sales_share=0.06,    # 최저가 안 팔림 → 마케팅 시장
            ),
            # --- 공기청정기 호환 필터: 중~대 시장 × 가치 싸움 → '최우선/틈새' 경계 ---
            CategoryObservation(
                category_name="공기청정기 호환 필터",
                discovery_pattern="호환소모품",
                base_device_bestseller_rank=8,
                base_device_search_volume=120_000,
                has_consumable=True,
                oem_price_krw=45_000,
                compatible_price_krw=16_000,
                repurchase_cycle_days=180,         # 반기 교체
                compatible_seller_count=30,
                oem_producible=True,
                top_products_review_counts=[3200, 2100, 1500, 900, 700],
                top_products_avg_price_krw=22_000,
                seller_count=160,
                category_search_volume=60_000,
                naver_ad_cpc_krw=650,
                brand_concentration=0.40,
                lowest_price_sales_share=0.33,
            ),
            # --- 가습기 호환 필터: 작은 시장 × 가치 싸움 → '틈새' 기대 ---
            CategoryObservation(
                category_name="가습기 호환 필터",
                discovery_pattern="호환소모품",
                base_device_bestseller_rank=25,
                base_device_search_volume=30_000,
                has_consumable=True,
                oem_price_krw=18_000,
                compatible_price_krw=6_000,
                repurchase_cycle_days=120,
                compatible_seller_count=8,
                oem_producible=True,
                top_products_review_counts=[600, 400, 250, 180, 120],
                top_products_avg_price_krw=9_000,   # 작은 시장(추정매출 낮음)
                seller_count=40,
                category_search_volume=15_000,
                naver_ad_cpc_krw=350,
                brand_concentration=0.25,
                lowest_price_sales_share=0.50,
            ),
            # --- 명품 향수: 작은~중 시장 × 광고 싸움 → '무시/함정' 기대 ---
            CategoryObservation(
                category_name="명품 향수",
                discovery_pattern="비소모품",
                base_device_bestseller_rank=None,
                base_device_search_volume=None,
                has_consumable=False,
                oem_price_krw=None,
                compatible_price_krw=None,
                repurchase_cycle_days=365,
                compatible_seller_count=None,
                oem_producible=False,              # OEM 부적합
                top_products_review_counts=[900, 700, 500, 300, 200],
                top_products_avg_price_krw=120_000,
                seller_count=300,
                category_search_volume=40_000,
                naver_ad_cpc_krw=1_900,            # 높은 CPC
                brand_concentration=0.85,
                lowest_price_sales_share=0.04,
            ),
        ]
