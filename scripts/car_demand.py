"""
car_demand.py — Phase C-3 차종 수요 규모 랭킹 (본 파이프라인 출력).

부품 시드(config.CAR_PART_SEEDS) 수확 → 차종 인식 → 모델별 합산 규모 랭킹.
config.MODEL_MIN_VOLUME 미만 컷. 정렬 규모순. 추세(C-4)는 별도 — 여기엔 없음.

실행: python scripts/car_demand.py   (검색광고 키 필요)
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
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
from src.adapters.naver_adapter import NaverAdapter
from src.core.car_demand import harvest_models, rank_models
from src.core.car_models import load_car_models


def main() -> None:
    idx = load_car_models()
    seeds = config.CAR_PART_SEEDS
    all_seeds = [s for ss in seeds.values() for s in ss]
    adapter = NaverAdapter(all_seeds)  # NAVER_AD_* 키 검증

    print("=" * 84)
    print("Phase C — 차종 수요 규모 랭킹  (규모만. 추세는 C-4 별도 컬럼 — 단일 점수 없음)")
    print("=" * 84)
    print("  [한계] · 차종별 부품 수요는 롱테일(수백~수천). 큰 시장과 자릿수가 다른 게 정상.")
    print("         · 검색 수요 ≠ 우리 판매량 — 시장 수요 우선순위 신호일 뿐.")
    print("         · '(세대미상)' 행은 세대 코드 없이 검색된 묶음 — 세대 판별은 사람 몫.")
    print(f"  시드: {seeds}")
    print(f"  컷: MODEL_MIN_VOLUME = {config.MODEL_MIN_VOLUME}  (미만 제외)")
    print()

    print("검색광고 키워드도구에서 부품 시드 수확 중...")
    agg = harvest_models(adapter, seeds, idx)
    rows = rank_models(agg, idx, config.MODEL_MIN_VOLUME)

    print()
    print(f"  {'정규명':<16} {'부품유형':<10} {'합산검색량':>10} {'멤버수':>5}  {'제조사':<10}")
    print(f"  {'-'*16} {'-'*10} {'-'*10} {'-'*5}  {'-'*10}")
    for r in rows:
        tag = " (세대미상)" if r.ambiguous else ""
        print(f"  {r.canonical:<16} {r.part_type:<10} {r.volume:>10,} {r.members:>5}  "
              f"{r.maker:<10}{tag}")

    concrete = sum(1 for r in rows if not r.ambiguous)
    ambiguous = sum(1 for r in rows if r.ambiguous)
    print(f"\n  → 랭킹 {len(rows)}행 (구체 세대 {concrete} / 세대미상 {ambiguous}) "
          f"· MODEL_MIN_VOLUME={config.MODEL_MIN_VOLUME} 이상")


if __name__ == "__main__":
    main()
