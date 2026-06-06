"""
car_demand_scanner.py — Phase C-2 검증 게이트 실험 (여기서 정지·보고용).

목적: 본 파이프라인(C-3+)을 만들기 전에, 부품 시드 수확이 '차종을 충분히 주는가'를
라이브로 확인한다. 합산 코어(src.core.search_volume)의 호출 경로·member_volume·dedupe 를
재사용하되, Phase C 는 소모품 필터가 아니라 '차종 인식'으로 묶으므로 aggregate_seed 는 쓰지
않는다(코어 무변경 — Phase B 보존).

출력(스펙 C-2):
  ① 인식된 distinct 모델 수 (+세대미상 모호 버킷 별도)
  ② 모델별 합산 규모 상위 30 (정규명 | 부품유형 | 합산검색량 | 멤버수)
  ③ 수확 키워드 중 모델 인식 비율 vs 버려진 비율 (건수·검색량)
  ④ 버려진 토큰 중 '모델처럼 보이는' 상위 30 (사전 누락 차종 발견용)

실행: python scripts/car_demand_scanner.py   (검색광고 키 필요)
★ C-3 이후는 보고 후 진행. 이 스크립트는 측정·정지 전용.
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
from src.core.car_models import load_car_models, normalize_text
from src.core.search_volume import dedupe_relkeywords, member_volume, FIELD_REL_KEYWORD

# ④ '모델처럼 보이는' 잔여 토큰 판별용 — 부품어 제거 후에도 흔한 비모델 토큰(노이즈).
_NOISE = [
    "교체", "교환", "주기", "방법", "가격", "비용", "정품", "순정", "추천", "후기",
    "호환", "규격", "사이즈", "종류", "위치", "청소", "냄새", "효과", "제거", "세트",
    "공임", "공임나라", "DIY", "디아이와이", "OEM", "오엠", "삼성서비스센터", "서비스센터",
    "자동차용품", "차량용품", "용품", "마스크", "필터망", "활성탄", "헤파", "교체비용",
]


def _model_like(residual: str) -> bool:
    """부품어 제거 잔여가 '차 모델처럼' 보이는가(거친 휴리스틱 — 사전 누락 발굴 보조)."""
    if not (2 <= len(residual) <= 10):
        return False
    return not any(n in residual for n in _NOISE)


def main() -> None:
    idx = load_car_models()
    seeds_by_type: dict[str, list[str]] = config.CAR_PART_SEEDS
    all_seeds = [(s, ptype) for ptype, ss in seeds_by_type.items() for s in ss]
    adapter = NaverAdapter([s for s, _ in all_seeds])  # NAVER_AD_* 키 검증

    print("=" * 88)
    print(f"[Phase C-2 검증 게이트] 부품 시드 수확 → 차종 인식 (사전 모델 {idx.model_count}개)")
    print(f"  시드: {all_seeds}")
    print("=" * 88)

    # 부품유형별로 연관어 수집(같은 유형 내 시드 교차 dedup — rel 1회만, 합산 부풀림 방지).
    per_type: dict[str, dict[str, int]] = {ptype: {} for ptype in seeds_by_type}
    for i, (seed, ptype) in enumerate(all_seeds):
        if i > 0:
            adapter._sleep(adapter.rate_limit_seconds)
        rows = adapter._request_keywordstool([seed])
        uniq = dedupe_relkeywords(rows)
        for rel, row in uniq.items():
            per_type[ptype].setdefault(rel, member_volume(row))
        print(f"  [{ptype}] 시드 '{seed}' → 연관어 {len(uniq)}개 수확")

    # 인식 집계.
    models: dict[tuple, dict] = {}   # (canonical, ptype) -> {volume, members, ambiguous}
    cnt = {"concrete": 0, "ambiguous": 0, "dropped": 0}
    vol = {"concrete": 0, "ambiguous": 0, "dropped": 0}
    dropped: list[tuple[str, int, str]] = []   # (rel, volume, ptype)
    total_kw = total_vol = 0

    for ptype, kwmap in per_type.items():
        for rel, v in kwmap.items():
            total_kw += 1
            total_vol += v
            r = idx.recognize(rel)
            if r.recognized:
                bucket = "ambiguous" if r.ambiguous else "concrete"
                cnt[bucket] += 1
                vol[bucket] += v
                key = (r.canonical, ptype)
                m = models.setdefault(key, {"volume": 0, "members": 0, "ambiguous": r.ambiguous})
                m["volume"] += v
                m["members"] += 1
            else:
                cnt["dropped"] += 1
                vol["dropped"] += v
                dropped.append((rel, v, ptype))

    concrete_models = {k[0] for k, m in models.items() if not m["ambiguous"]}
    ambiguous_buckets = {k[0] for k, m in models.items() if m["ambiguous"]}

    # ① distinct 모델 수
    print("\n" + "─" * 88)
    print(f"① 인식된 distinct 모델: {len(concrete_models)}개 (구체 세대) "
          f"+ 모호 버킷 {len(ambiguous_buckets)}개 (세대미상)")
    print(f"   (모델×부품유형 행: {len(models)}개)")

    # ② 모델별 합산 규모 상위 30
    print("\n" + "─" * 88)
    print("② 모델별 합산 규모 상위 30  (정규명 | 부품유형 | 합산검색량 | 멤버수)")
    print(f"   {'정규명':<16} {'부품유형':<10} {'합산검색량':>10} {'멤버수':>5}")
    rows_sorted = sorted(models.items(), key=lambda kv: kv[1]["volume"], reverse=True)
    for (canon, ptype), m in rows_sorted[:30]:
        tag = " (세대미상)" if m["ambiguous"] else ""
        print(f"   {canon:<16} {ptype:<10} {m['volume']:>10,} {m['members']:>5}{tag}")

    # ③ 인식/버림 비율
    print("\n" + "─" * 88)
    print("③ 수확 키워드 인식 비율 (건수 / 검색량)")
    def _pct(part, whole):
        return (part / whole * 100) if whole else 0.0
    print(f"   전체 수확: {total_kw}건 / {total_vol:,}검색량")
    print(f"   모델 인식(구체): {cnt['concrete']}건 ({_pct(cnt['concrete'], total_kw):.1f}%) / "
          f"{vol['concrete']:,} ({_pct(vol['concrete'], total_vol):.1f}%)")
    print(f"   모델 인식(세대미상): {cnt['ambiguous']}건 ({_pct(cnt['ambiguous'], total_kw):.1f}%) / "
          f"{vol['ambiguous']:,} ({_pct(vol['ambiguous'], total_vol):.1f}%)")
    print(f"   버림(비모델): {cnt['dropped']}건 ({_pct(cnt['dropped'], total_kw):.1f}%) / "
          f"{vol['dropped']:,} ({_pct(vol['dropped'], total_vol):.1f}%)")

    # ④ 버려진 토큰 중 '모델처럼 보이는' 상위 30
    print("\n" + "─" * 88)
    print("④ 버려진 토큰 중 '모델처럼 보이는' 상위 30 (사전 누락 차종 발견용 — 노이즈 섞임, 시경 눈검사)")
    print(f"   {'원본 키워드':<28} {'잔여(부품어제거)':<16} {'검색량':>9} 부품유형")
    candidates = []
    for rel, v, ptype in dropped:
        residual = idx.strip_parts(rel)
        if _model_like(residual):
            candidates.append((rel, residual, v, ptype))
    candidates.sort(key=lambda x: x[2], reverse=True)
    for rel, residual, v, ptype in candidates[:30]:
        print(f"   {rel:<28} {residual:<16} {v:>9,} {ptype}")

    print("\n" + "=" * 88)
    print("[정지] C-2 검증 게이트. 위 결과로 ① 수확이 모델을 충분히 주는가 "
          "② 사전 구멍은 어디인가 판단. C-3 이후는 보고 후 진행.")


if __name__ == "__main__":
    main()
