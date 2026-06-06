"""
diagnose_naver.py — NaverAdapter 발굴 파이프라인 단계별 raw 진단 (비침습).

기존 동작은 일절 바꾸지 않는다. 어댑터의 실제 메서드를 그대로 호출해
프로덕션과 동일한 경로로 중간값을 찍어, 후보가 어디서 0이 되는지 눈으로 확인한다.

실행(프로젝트 루트에서, 실제 키 필요):
    python scripts/diagnose_naver.py
    python scripts/diagnose_naver.py 에어컨필터 와이퍼 공기청정기   # 시드 직접 지정

키는 .env(NAVER_AD_API_KEY / NAVER_AD_SECRET_KEY / NAVER_AD_CUSTOMER_ID)에서 읽는다.
미설정 시 NaverAdapter 가 어느 키가 없는지 명시 예외를 던진다(조용한 폴백 없음).

찍는 것(시드별):
  1) 응답 연관어 수(dedupe 전/후)
  2) 멤버별 comp_idx · plAvgDepth 개별 값 전수 나열(소모품으로 잡힌 것)
     + 소모품 사전에 안 걸린 연관어 상위(누락 점검용, 예: 와이퍼 '워셔액')
  3) 집계된 comp_idx · avg_ad_depth(_make_device_group 결과)
  4) 신호7 합산값, 신호8 판정(is_ad_war + 사유), 4분면, 기본 랭킹 포함 여부
"""

from __future__ import annotations

import os
import sys
import time

# 프로젝트 루트를 import 경로에 추가(스크립트를 어디서 실행하든 동작).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 윈도우 콘솔 한글 깨짐 방지.
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
from src.adapters.csv_adapter import _is_consumable, _parse_volume
from src.adapters.naver_adapter import (
    _FIELD_AD_DEPTH,
    _FIELD_COMP_IDX,
    _FIELD_MONTHLY_MOBILE,
    _FIELD_MONTHLY_PC,
    _FIELD_REL_KEYWORD,
    NaverAdapter,
)
from src.ranking import build_candidate, rank_categories
from src.signals import compute_all_signals, detect_ad_war
from src.winnability import classify_quadrant

DEFAULT_SEEDS = ["에어컨필터", "와이퍼", "공기청정기"]
_TOP_NONCONSUMABLE_TO_SHOW = 25  # 누락 점검용으로 보여줄 비매칭 연관어 수


def _vol(item: dict) -> int:
    return int(
        _parse_volume(item.get(_FIELD_MONTHLY_PC))
        + _parse_volume(item.get(_FIELD_MONTHLY_MOBILE))
    )


def diagnose_seed(adapter: NaverAdapter, seed: str):
    """시드 1개를 단계별로 진단 출력하고, 만들어진 CategoryCandidate(없으면 None) 반환."""
    print("=" * 78)
    print(f"시드: {seed}")
    print("=" * 78)

    # --- 1) 실제 API 호출(raw) ---
    raw = adapter._request_keywordstool([seed])
    print(f"[1] 응답 연관어 수(raw): {len(raw)}")

    # relKeyword 단위 dedupe(fetch 와 동일).
    seen: dict[str, dict] = {}
    for it in raw:
        rel = str(it.get(_FIELD_REL_KEYWORD) or "").strip()
        if rel and rel not in seen:
            seen[rel] = it
    print(f"    dedupe 후 고유 연관어 수: {len(seen)}")

    consumables = [(rel, it) for rel, it in seen.items() if _is_consumable(rel)]
    non_consumables = [(rel, it) for rel, it in seen.items() if not _is_consumable(rel)]
    print(f"    소모품 사전 통과: {len(consumables)} / 미통과: {len(non_consumables)}")

    if not consumables:
        print("\n[!] 소모품 멤버 0개 → 이 시드는 관측치 자체가 안 생김(여기서 0).")
        # 아래 [2] 누락 점검은 그대로 보여주고 종료 처리는 함수 끝에서.

    # --- 2) 멤버별 comp_idx · plAvgDepth 전수 나열 ---
    print(f"\n[2] 소모품 멤버 개별 분포({len(consumables)}개) "
          f"— keyword | 검색량(PC+모바일) | compIdx | plAvgDepth")
    for rel, it in sorted(consumables, key=lambda x: _vol(x[1]), reverse=True):
        print(f"    {rel:<24} {str(_vol(it)):>9}  "
              f"comp={str(it.get(_FIELD_COMP_IDX)):<4} "
              f"depth={it.get(_FIELD_AD_DEPTH)}")

    # 누락 점검: 소모품으로 안 잡힌 상위 연관어(예: 와이퍼 '워셔액').
    shown = sorted(non_consumables, key=lambda x: _vol(x[1]), reverse=True)[
        :_TOP_NONCONSUMABLE_TO_SHOW
    ]
    if shown:
        print(f"\n    [참고] 소모품 사전 미통과 상위 {len(shown)}개(누락 후보 점검용):")
        for rel, it in shown:
            print(f"      - {rel:<24} {str(_vol(it)):>9}")

    if not consumables:
        print()
        return None

    # --- 3) 집계값(_make_device_group, 프로덕션 경로) ---
    obs = adapter._make_device_group(seed, [it for _, it in consumables])
    print(f"\n[3] 집계(_make_device_group):")
    print(f"    집계 comp_idx   = {obs.comp_idx}")
    print(f"    집계 avg_ad_depth = {obs.avg_ad_depth}")

    # --- 4) 신호/4분면/랭킹 포함 여부 ---
    s = compute_all_signals(obs)
    is_ad_war, reasons = detect_ad_war(obs)
    quadrant = classify_quadrant(s["_market_size_bucket"], s["_is_ad_war"])
    print(f"\n[4] 신호 & 4분면:")
    print(f"    신호7 합산 검색량 = {obs.category_search_volume:,} "
          f"(점수 {s['signal_7_market_size']:.3f}, 시장규모 {s['_market_size_bucket']})")
    print(f"    신호8 판정 is_ad_war = {is_ad_war}  사유={reasons}  "
          f"(점수 {s['signal_8_winnability']:.3f})")
    print(f"    4분면 = {quadrant}")
    excluded = quadrant in ("함정", "무시")
    print(f"    기본 랭킹(include_trap=False) 포함? {'아니오(제외됨)' if excluded else '예'}"
          f"{'  ← 여기서 0이 됨' if excluded else ''}")
    print()
    return build_candidate(obs)


def main() -> None:
    seeds = sys.argv[1:] or DEFAULT_SEEDS
    print(f"진단 시드: {seeds}")
    print(f"경계: LARGE={config.MARKET_SIZE_LARGE_SEARCHVOL:,} "
          f"MEDIUM={config.MARKET_SIZE_MEDIUM_SEARCHVOL:,} | "
          f"AD_DEPTH_HIGH={config.AD_DEPTH_HIGH_THRESHOLD} "
          f"COMP_AD_WAR={config.COMP_IDX_AD_WAR_VALUES} "
          f"FLAG_COUNT={config.AD_WAR_SIGNAL_COUNT_TO_FLAG}\n")

    # 키 검증은 생성자가 수행(미설정 시 어느 키가 없는지 명시 예외).
    adapter = NaverAdapter(seeds)

    # 시드당 1회만 호출(API 절약) — 여기서 모은 관측치로 아래 랭킹 요약을 재계산.
    candidates = []
    for i, seed in enumerate(seeds):
        if i > 0:
            time.sleep(adapter.rate_limit_seconds)  # 호출 간 rate limit
        try:
            c = diagnose_seed(adapter, seed)
            if c is not None:
                candidates.append(c)
        except Exception as e:  # noqa: BLE001 — 어떤 시드에서 깨졌는지 명시하고 계속.
            print(f"[!] 시드 '{seed}' 진단 중 오류: {type(e).__name__}: {e}\n")

    # 종합: 위에서 모은 후보로 랭킹 필터 적용(추가 API 호출 없음 — rank_categories 는 순수).
    print("=" * 78)
    print("종합: 랭킹 필터 결과(실제 화면과 동일)")
    print("=" * 78)
    ranked_def = rank_categories(candidates, include_trap=False)
    ranked_all = rank_categories(candidates, include_trap=True)
    print(f"  기본(함정 제외) 랭킹: {len(ranked_def)}개")
    print(f"  함정 포함 랭킹      : {len(ranked_all)}개")
    for c in ranked_all:
        mark = "" if c in ranked_def else "  (기본 제외)"
        print(f"    - {c.category_name}: {c.winnability}{mark}")


if __name__ == "__main__":
    main()
