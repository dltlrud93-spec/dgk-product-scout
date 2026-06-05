"""
signals.py — 발굴 신호 8개 산출 로직 (스펙 3.2).

각 신호를 0.0~1.0 점수로 정규화한다. 원시 관측치(CategoryObservation)를
config.py 의 경계로 점수화한다. 데이터가 없으면(None) 보수적으로 처리하고
(스펙 6절 #2 "데이터 부족은 통과시키지 않는다") 메타에 표시한다.

신호 7(시장 규모)·8(진입 가능성)은 점수뿐 아니라 4분면 판정에 쓰는
파생 메타(추정 매출, 광고싸움 여부)도 함께 산출한다.
"""

from __future__ import annotations

import config
from src.schema import CategoryObservation


def _clamp01(x: float) -> float:
    """0.0~1.0 범위로 절단."""
    return max(0.0, min(1.0, x))


def _linear_score(value: float, strong: float, weak: float) -> float:
    """
    value 가 strong 쪽이면 1.0, weak 쪽이면 0.0 으로 선형 보간.
    strong < weak 이든 strong > weak 이든 모두 처리한다.
    """
    if strong == weak:
        return 1.0 if value == strong else 0.0
    return _clamp01((value - weak) / (strong - weak))


# ---------------------------------------------------------------------------
# 신호 1 — 베이스 기기 인기/보유 규모
# ---------------------------------------------------------------------------
def signal_1_base_device_popularity(obs: CategoryObservation) -> float:
    # 베스트셀러 순위(작을수록 인기) + 검색량을 결합. 베이스 기기 개념이
    # 없는 카테고리(화장품 등)는 0.0 (베이스 기기 소모품 패턴 아님).
    if obs.base_device_bestseller_rank is None and obs.base_device_search_volume is None:
        return 0.0
    rank = obs.base_device_bestseller_rank
    # 순위 1위 → 1.0, 50위 → 0.0 (선형). 데이터 없으면 0.5 중립.
    rank_score = _linear_score(rank, strong=1, weak=50) if rank is not None else 0.5
    vol = obs.base_device_search_volume
    # 검색량 200k → 1.0, 0 → 0.0. 데이터 없으면 0.5 중립.
    vol_score = _linear_score(vol, strong=200_000, weak=0) if vol is not None else 0.5
    return _clamp01((rank_score + vol_score) / 2)


# ---------------------------------------------------------------------------
# 신호 2 — 소모품 존재
# ---------------------------------------------------------------------------
def signal_2_consumable_exists(obs: CategoryObservation) -> float:
    # 정기 교체 부품이 있어야 호환 소모품 발굴 패턴이 성립(게이트성).
    return 1.0 if obs.has_consumable else 0.0


# ---------------------------------------------------------------------------
# 신호 3 — 정품 가격 갭 (갭 클수록 기회)
# ---------------------------------------------------------------------------
def signal_3_price_gap(obs: CategoryObservation) -> float:
    if not obs.oem_price_krw or obs.compatible_price_krw is None:
        return 0.0  # 데이터 부족 → 보수적 0점
    if obs.oem_price_krw <= 0:
        return 0.0
    gap_ratio = (obs.oem_price_krw - obs.compatible_price_krw) / obs.oem_price_krw
    # 갭 비율 0 → 0.0, PRICE_GAP_RATIO_STRONG → 1.0.
    return _linear_score(gap_ratio, strong=config.PRICE_GAP_RATIO_STRONG, weak=0.0)


# ---------------------------------------------------------------------------
# 신호 4 — 재구매 주기 (짧을수록 회전 엔진)
# ---------------------------------------------------------------------------
def signal_4_repurchase_cycle(obs: CategoryObservation) -> float:
    if obs.repurchase_cycle_days is None:
        return 0.0
    return _linear_score(
        obs.repurchase_cycle_days,
        strong=config.REPURCHASE_DAYS_STRONG,   # 짧음 → 1.0
        weak=config.REPURCHASE_DAYS_WEAK,        # 김 → 0.0
    )


# ---------------------------------------------------------------------------
# 신호 5 — 호환 경쟁 강도 (셀러 적을수록 공백 = 기회)
# ---------------------------------------------------------------------------
def signal_5_compat_competition(obs: CategoryObservation) -> float:
    if obs.compatible_seller_count is None:
        return 0.0
    return _linear_score(
        obs.compatible_seller_count,
        strong=config.COMPAT_SELLERS_STRONG,     # 적음 → 1.0
        weak=config.COMPAT_SELLERS_WEAK,         # 포화 → 0.0
    )


# ---------------------------------------------------------------------------
# 신호 6 — 우리 역량 적합 (중국 OEM 생산 가능)
# ---------------------------------------------------------------------------
def signal_6_our_capability(obs: CategoryObservation) -> float:
    # 스펙: "거의 항상 충족". OEM 생산 가능 여부로 단순 판정.
    return 1.0 if obs.oem_producible else 0.0


# ---------------------------------------------------------------------------
# 신호 7 — 시장 규모/수요 (추정)
# ---------------------------------------------------------------------------
def estimate_market_revenue(obs: CategoryObservation) -> float:
    """
    상위 상품 (리뷰수 × 평균가) 합으로 매출을 근사한다(스펙 3.2: 추정치).
    정밀 TAM 아님. 데이터 없으면 0.0.
    """
    if not obs.top_products_review_counts or not obs.top_products_avg_price_krw:
        return 0.0
    total_reviews = sum(obs.top_products_review_counts)
    return total_reviews * obs.top_products_avg_price_krw


def market_size_bucket(revenue_est: float) -> str:
    """추정 매출(KRW) → 대/중/소."""
    if revenue_est >= config.MARKET_SIZE_LARGE_KRW:
        return "대"
    if revenue_est >= config.MARKET_SIZE_MEDIUM_KRW:
        return "중"
    return "소"


def market_size_bucket_by_search_volume(search_volume: float) -> str:
    """월 검색량(절대) → 대/중/소. (CSV/키워드도구 소스용)"""
    if search_volume >= config.MARKET_SIZE_LARGE_SEARCHVOL:
        return "대"
    if search_volume >= config.MARKET_SIZE_MEDIUM_SEARCHVOL:
        return "중"
    return "소"


def resolve_market_size(obs: CategoryObservation) -> tuple[float, str, str]:
    """
    시장 규모를 두 가지 소스 중 가용한 것으로 산출한다.

    - 리뷰×가격 데이터가 있으면(mock 등): '추정매출' 기준(KRW 경계).
    - 없고 검색량이 있으면(CSV/키워드도구): '검색량' 기준(검색수 경계).
      ※ 데이터랩 상대값(최대100)이 아니라 키워드도구의 '절대 월간검색수'여야 한다(스펙 3.2).

    반환: (지표값, 기준라벨, 대/중/소 버킷).
    데이터가 둘 다 없으면 (0, "없음", "소").
    """
    revenue = estimate_market_revenue(obs)
    if revenue > 0:
        return revenue, "추정매출", market_size_bucket(revenue)
    if obs.category_search_volume:
        sv = float(obs.category_search_volume)
        return sv, "검색량", market_size_bucket_by_search_volume(sv)
    return 0.0, "없음", "소"


def signal_7_market_size(obs: CategoryObservation) -> float:
    # 가용 소스에 맞는 '대' 경계로 0~1 정규화(대 경계 이상이면 만점).
    metric, basis, _ = resolve_market_size(obs)
    if basis == "검색량":
        return _linear_score(metric, strong=config.MARKET_SIZE_LARGE_SEARCHVOL, weak=0.0)
    return _linear_score(metric, strong=config.MARKET_SIZE_LARGE_KRW, weak=0.0)


# ---------------------------------------------------------------------------
# 신호 8 — 진입 가능성 / 광고 의존도
# ---------------------------------------------------------------------------
def detect_ad_war(obs: CategoryObservation) -> tuple[bool, list[str]]:
    """
    광고 싸움(우리 불리) 여부 판정(스펙 3.3).

    소스에 따라 평가 가능한 신호가 다르다(없는 필드는 None → 평가 생략):
      - 마켓/가공 소스: 높은 CPC / 브랜드 집중 / 최저가 안 팔림.
      - 검색광고 키워드도구·CSV 소스: 경쟁정도(compIdx) / 월평균노출광고수(plAvgDepth).
    config.AD_WAR_SIGNAL_COUNT_TO_FLAG 개 이상 충족 시 광고 싸움으로 분류한다.
    충족한 사유 리스트도 함께 반환(설명용).
    """
    reasons: list[str] = []
    if obs.naver_ad_cpc_krw is not None and obs.naver_ad_cpc_krw >= config.CPC_HIGH_THRESHOLD_KRW:
        reasons.append(f"높은 CPC({obs.naver_ad_cpc_krw:,.0f}원)")
    if obs.brand_concentration is not None and obs.brand_concentration >= config.BRAND_CONCENTRATION_HIGH:
        reasons.append(f"브랜드 집중({obs.brand_concentration:.0%})")
    if (
        obs.lowest_price_sales_share is not None
        and obs.lowest_price_sales_share < config.LOWEST_PRICE_SHARE_FLOOR
    ):
        reasons.append(f"최저가 안 팔림({obs.lowest_price_sales_share:.0%})")
    # --- 검색광고 키워드도구/CSV 소스 신호 ---
    if obs.comp_idx is not None and obs.comp_idx in config.COMP_IDX_AD_WAR_VALUES:
        reasons.append(f"경쟁정도 {obs.comp_idx}")
    if obs.avg_ad_depth is not None and obs.avg_ad_depth >= config.AD_DEPTH_HIGH_THRESHOLD:
        reasons.append(f"노출광고 많음({obs.avg_ad_depth:.1f})")
    is_ad_war = len(reasons) >= config.AD_WAR_SIGNAL_COUNT_TO_FLAG
    return is_ad_war, reasons


def signal_8_winnability(obs: CategoryObservation) -> float:
    """
    진입 가능성 점수: 광고 싸움이면 낮고, 가치 싸움이면 높다.
    충족된 광고싸움 신호 개수를 '소스별 평가 가능 신호 수'로 정규화해 감점한다.
    (마켓 소스는 3개, CSV/키워드도구 소스는 2개가 평가 가능)
    """
    _, reasons = detect_ad_war(obs)
    evaluable = _evaluable_ad_war_count(obs)
    if evaluable == 0:
        return 1.0  # 광고싸움을 평가할 데이터가 없으면 감점하지 않음(보류 성격)
    return _clamp01(1.0 - len(reasons) / evaluable)


def _evaluable_ad_war_count(obs: CategoryObservation) -> int:
    """해당 관측치에서 평가 가능한(값이 채워진) 광고싸움 신호 개수."""
    fields = (
        obs.naver_ad_cpc_krw,
        obs.brand_concentration,
        obs.lowest_price_sales_share,
        obs.comp_idx,
        obs.avg_ad_depth,
    )
    return sum(1 for f in fields if f is not None)


# ---------------------------------------------------------------------------
# 통합 산출
# ---------------------------------------------------------------------------
def compute_all_signals(obs: CategoryObservation) -> dict:
    """
    신호 1~8 점수 + 4분면 판정에 필요한 파생 메타를 한 번에 산출.
    signal_scores dict(스펙 3.4) 형태로 반환.
    """
    market_metric, market_basis, market_bucket = resolve_market_size(obs)
    is_ad_war, ad_war_reasons = detect_ad_war(obs)
    return {
        "signal_1_base_device_popularity": signal_1_base_device_popularity(obs),
        "signal_2_consumable_exists": signal_2_consumable_exists(obs),
        "signal_3_price_gap": signal_3_price_gap(obs),
        "signal_4_repurchase_cycle": signal_4_repurchase_cycle(obs),
        "signal_5_compat_competition": signal_5_compat_competition(obs),
        "signal_6_our_capability": signal_6_our_capability(obs),
        "signal_7_market_size": signal_7_market_size(obs),
        "signal_8_winnability": signal_8_winnability(obs),
        # --- 4분면/설명용 파생 메타 ---
        "_market_metric": market_metric,
        "_market_size_basis": market_basis,   # 추정매출 | 검색량 | 없음
        "_market_size_bucket": market_bucket,
        "_is_ad_war": is_ad_war,
        "_ad_war_reasons": ad_war_reasons,
        # --- 디버그용 원본값(정규화 전 raw) ---
        #   정규화 점수만으로는 경계 적절성을 못 보므로 원본을 함께 노출한다.
        #   신호 7 = '기기군 합산' monthlyPcQcCnt + monthlyMobileQcCnt(절대 월간검색수).
        "_raw_search_volume": obs.category_search_volume,
        "_raw_comp_idx": obs.comp_idx,            # 경쟁정도(낮음/중간/높음)
        "_raw_avg_ad_depth": obs.avg_ad_depth,    # 월평균노출광고수(plAvgDepth)
        # 보조표시: 기기군 합산을 구성한 개별 연관키워드 내역(NaverAdapter 만 채움).
        "_member_keywords": obs.member_keywords,
    }
