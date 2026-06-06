"""
diagnose_winter_market.py — '겨울 차량 케어' 시장의 진짜 합산 규모 재확인(검수형 진단).

배경: seasonal_calendar 는 '성에제거제' 같은 단일 키워드만 봐서 월검색 30~80 으로 작게
나온다. 그러나 에어컨필터 전례처럼(단일 작아도 기기군 합산 34만) 호환/연관 키워드를
합치면 시장은 클 수 있다. 여기서는 '규모만' 합산으로 재확인한다(계절성과는 독립).

[중요] 기존 NaverAdapter 의 기기군 합산은 연관어를 _is_consumable 로 거르는데, 그 사전
(config.CONSUMABLE_KEYWORDS)은 '청소기 소모품' 전용(필터/브러시/헤파/먼지봉투/패드…)이라
겨울 키워드(성에제거제/워셔액/부동액/김서림방지제/타이어체인 …)를 거의 다 탈락시킨다.
실제로 기존 diagnose_naver.py 도 '워셔액'을 소모품 사전 미통과(누락) 후보로 적어 두었다.
→ 그래서 여기서는 필터를 '겨울 관련성 allowlist(WINTER_TOKENS)'로 교체한다.
   재사용: API 호출(서명/429 백오프)·_parse_volume·_aggregate_comp_idx 는 그대로.
   교체  : _is_consumable → 겨울 토큰 필터, 시드별 합산 → 시드 '교차 dedup' 합산.

검수형: 멤버를 전수 출력하고, allowlist 미통과 고검색 연관어(누락 점검)도 같이 보여준다.
        비교용으로 기존 _is_consumable 적용 합산도 표기(겨울엔 거의 0 → 사전 부적합 확인).

실행(프로젝트 루트, 검색광고 키 필요):
    python scripts/diagnose_winter_market.py
    python scripts/diagnose_winter_market.py 성에제거 김서림   # 시드 직접 지정
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import config
from src.adapters.csv_adapter import _aggregate_comp_idx, _is_consumable, _parse_volume
from src.adapters.naver_adapter import (
    _FIELD_AD_DEPTH,
    _FIELD_COMP_IDX,
    _FIELD_MONTHLY_MOBILE,
    _FIELD_MONTHLY_PC,
    _FIELD_REL_KEYWORD,
    NaverAdapter,
)

# ─────────────────────────────────────────────────────────────────────────────
# 설정 (결과 보고 조정)
# ─────────────────────────────────────────────────────────────────────────────

# 겨울 차량 케어 합산 시드(시드당 1회 호출 → 연관어 수확).
WINTER_SEEDS = [
    "성에제거", "김서림", "스노우체인", "워셔액",
    "부동액", "스노우브러쉬", "결빙방지", "제설",  # '제설'은 도로용 노이즈 가능 → 검수 표시
]

# 겨울 관련성 allowlist 토큰(_is_consumable 대체). 부분문자열 매칭. 3그룹으로 분리:
#
#  · SEASON_CORE  : 겨울 '전용' 시즌 품목. 12월±1 정점이 분명한 진짜 계절 수요.
#  · ALWAYS_ON    : 와이퍼·워셔. 0/1단계에서 '상시상품'(지수 1.2~1.7)로 확인됨 →
#                   겨울 시장으로 잡히지만 '연중 수요'라 코어와 분리해서 본다.
#  · GENERIC      : '제거제/방지제' 접미사. 성에제거제·김서림방지제는 코어 토큰으로
#                   이미 잡히므로, 이것'만'으로 걸린 건 스티커/유막제거제 같은 비겨울
#                   노이즈일 수 있다 → '기타검수' 버킷으로 떼어 사람이 확인.
SEASON_CORE_TOKENS = [
    "성에", "서리", "김서림", "스노우", "제설", "결빙", "동결", "해빙", "제빙",
    "부동액", "냉각수", "체인", "스크래퍼", "스크레이퍼", "긁개", "안티포그",
    "방한", "눈삽", "열선", "온열", "발열",
]
ALWAYS_ON_TOKENS = ["와이퍼", "워셔"]
GENERIC_TOKENS = ["제거제", "방지제"]

# 전체 '겨울 관련' 판정용(세 그룹 합집합).
WINTER_TOKENS = SEASON_CORE_TOKENS + ALWAYS_ON_TOKENS + GENERIC_TOKENS

_TOP_DROPPED_TO_SHOW = 25  # 누락 점검용으로 보여줄 allowlist 미통과 상위 연관어 수
_TOP_MEMBERS_TO_SHOW = 30  # 버킷별 멤버 출력 상위 개수(합산은 전체 기준)


def _is_winter(keyword: str) -> bool:
    return any(tok in keyword for tok in WINTER_TOKENS)


def _bucket(keyword: str) -> str:
    """겨울 관련 키워드를 시즌코어/상시/기타검수로 분류(코어 우선)."""
    if any(t in keyword for t in SEASON_CORE_TOKENS):
        return "시즌코어"
    if any(t in keyword for t in ALWAYS_ON_TOKENS):
        return "상시"
    return "기타검수"  # GENERIC 접미사만 걸린 것


def _vol(row: dict) -> int:
    return int(
        _parse_volume(row.get(_FIELD_MONTHLY_PC))
        + _parse_volume(row.get(_FIELD_MONTHLY_MOBILE))
    )


def main() -> None:
    seeds = sys.argv[1:] or WINTER_SEEDS
    adapter = NaverAdapter(seeds)  # 생성자가 NAVER_AD_* 키 검증(없으면 명시 예외)

    print(f"겨울 차량 케어 시장 합산 재확인 — 시드 {len(seeds)}개: {seeds}")
    print(f"규모 경계(참고): 대 ≥ {config.MARKET_SIZE_LARGE_SEARCHVOL:,} / "
          f"중 ≥ {config.MARKET_SIZE_MEDIUM_SEARCHVOL:,} (기기군 합산 기준)\n")

    # 시드별 수확 → 시드 교차 dedup(연관어 중복은 1회만, 합산 부풀림 방지).
    # uniq[normalized_keyword] = {"rel":, "row":, "vol":, "seeds": set()}
    uniq: dict[str, dict] = {}
    for i, seed in enumerate(seeds):
        if i > 0:
            adapter._sleep(adapter.rate_limit_seconds)  # 호출 간 rate limit
        try:
            rows = adapter._request_keywordstool([seed])
        except Exception as e:  # noqa: BLE001 — 어느 시드에서 깨졌는지 알리고 계속
            print(f"[!] 시드 '{seed}' 호출 실패: {type(e).__name__}: {e}")
            continue

        winter_in_seed = 0
        for row in rows:
            rel = str(row.get(_FIELD_REL_KEYWORD) or "").strip()
            if not rel:
                continue
            key = "".join(rel.split())  # 공백 제거 정규화
            slot = uniq.setdefault(
                key, {"rel": rel, "row": row, "vol": _vol(row), "seeds": set()}
            )
            slot["seeds"].add(seed)
            if _is_winter(rel):
                winter_in_seed += 1
        print(f"  [{seed}] 연관어 {len(rows)}개 수확 (겨울 토큰 통과 {winter_in_seed}개)")

    # 분류: 겨울 관련 / 미관련 → 겨울 관련은 다시 시즌코어/상시/기타검수.
    winter = [v for v in uniq.values() if _is_winter(v["rel"])]
    dropped = [v for v in uniq.values() if not _is_winter(v["rel"])]
    dropped.sort(key=lambda v: v["vol"], reverse=True)

    buckets: dict[str, list[dict]] = {"시즌코어": [], "상시": [], "기타검수": []}
    for v in winter:
        buckets[_bucket(v["rel"])].append(v)
    for lst in buckets.values():
        lst.sort(key=lambda v: v["vol"], reverse=True)

    def _size_bucket(total: int) -> str:
        if total >= config.MARKET_SIZE_LARGE_SEARCHVOL:
            return "대"
        return "중" if total >= config.MARKET_SIZE_MEDIUM_SEARCHVOL else "소"

    sums = {name: sum(v["vol"] for v in lst) for name, lst in buckets.items()}
    core_total = sums["시즌코어"]
    core_plus_always = sums["시즌코어"] + sums["상시"]
    grand_total = sum(sums.values())

    # --- 버킷별 멤버(상위) + 합산 ---
    print("\n" + "=" * 78)
    print("[A] 겨울 관련 연관어 — 시즌코어 vs 상시 vs 기타검수 (시드 교차 dedup)")
    print("=" * 78)
    for name in ("시즌코어", "상시", "기타검수"):
        lst = buckets[name]
        note = {
            "시즌코어": "겨울 전용 시즌 수요",
            "상시": "와이퍼·워셔 — 연중 수요(0/1단계서 상시상품 확인)",
            "기타검수": "제거제/방지제 접미사만 매칭 — 비겨울 노이즈 가능",
        }[name]
        print(f"\n  ── [{name}] {len(lst)}개, 합산 {sums[name]:,}  ({note})")
        print(f"     {'키워드':<24} {'월검색(PC+모바일)':>14}   출처시드")
        for v in lst[:_TOP_MEMBERS_TO_SHOW]:
            print(f"     {v['rel']:<24} {v['vol']:>14,}   {','.join(sorted(v['seeds']))}")
        if len(lst) > _TOP_MEMBERS_TO_SHOW:
            rest = sum(x["vol"] for x in lst[_TOP_MEMBERS_TO_SHOW:])
            print(f"     … 외 {len(lst) - _TOP_MEMBERS_TO_SHOW}개 합 {rest:,}")

    print("\n" + "-" * 78)
    print(f"  ▶ 시즌코어 합산        = {core_total:>10,}  → 규모 '{_size_bucket(core_total)}'"
          f"   (겨울 전용 진짜 시장)")
    print(f"  ▶ 시즌코어+상시 합산   = {core_plus_always:>10,}  → 규모 "
          f"'{_size_bucket(core_plus_always)}'   (와이퍼·워셔 포함)")
    print(f"  ▶ 전체(+기타검수)      = {grand_total:>10,}  → 규모 "
          f"'{_size_bucket(grand_total)}'   (검수 전 거친 상한)")
    print(f"  [경계] 대 ≥ {config.MARKET_SIZE_LARGE_SEARCHVOL:,} / "
          f"중 ≥ {config.MARKET_SIZE_MEDIUM_SEARCHVOL:,}")

    # 신호8 보조(경쟁정도/광고깊이) — 시즌코어 기준 참고용.
    core = buckets["시즌코어"]
    comp = _aggregate_comp_idx([str(v["row"].get(_FIELD_COMP_IDX) or "") for v in core])
    depths = [_parse_volume(v["row"].get(_FIELD_AD_DEPTH)) for v in core]
    avg_depth = (sum(depths) / len(depths)) if depths else None
    if avg_depth is not None:
        print(f"  (참고·시즌코어) 대표 경쟁정도={comp}  평균 노출광고수={avg_depth:.1f}")

    # --- 누락 점검: allowlist 미통과 고검색 연관어 ---
    shown = dropped[:_TOP_DROPPED_TO_SHOW]
    print("\n" + "=" * 78)
    print(f"[B] 누락 점검 — allowlist 미통과 상위 {len(shown)}개 "
          f"(겨울 키워드를 놓쳤는지 눈으로 확인)")
    print("=" * 78)
    for v in shown:
        print(f"  - {v['rel']:<26} {v['vol']:>14,}")

    # --- 비교: 기존 _is_consumable 적용 시 합산(겨울엔 거의 0 예상) ---
    cons = [v for v in uniq.values() if _is_consumable(v["rel"])]
    cons_total = sum(v["vol"] for v in cons)
    print("\n" + "=" * 78)
    print("[C] 비교 — 기존 _is_consumable(청소기 소모품 사전) 적용 시")
    print("=" * 78)
    print(f"  통과 키워드 {len(cons)}개, 합산 {cons_total:,}")
    if cons:
        for v in sorted(cons, key=lambda x: x["vol"], reverse=True)[:10]:
            print(f"    · {v['rel']:<26} {v['vol']:>14,}")
    print(f"  → 겨울 전체({grand_total:,})의 {cons_total / grand_total * 100:.1f}% 만 포착"
          if grand_total else "  → (겨울 합산 0)")
    print("  ∴ 청소기 사전은 겨울 차량용품 합산에 부적합 — allowlist 교체가 타당.")


if __name__ == "__main__":
    main()
