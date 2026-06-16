"""
diagnose_jogyeonpyo.py — 조견표 모드 중간 규모 검증(429·소요시간·신차 포함).

UI 와 '동일한 코어'(jogyeonpyo.harvest_jogyeonpyo_kw_items + teamp 블로그 조회)를 그대로 돌려
429 건수·소요시간을 실측한다. 전체(221/452) 풀기 전, config.JOGYEONPYO_TEST_LIMIT(기본 50)로만.

실행:
  python scripts/diagnose_jogyeonpyo.py [SHEET_ID] [LIMIT]
  · SHEET_ID 생략 시 env JOGYEONPYO_SHEET_ID 사용.
  · LIMIT  생략 시 config.JOGYEONPYO_TEST_LIMIT(50).

필요 키: 구글시트(서비스계정) + NAVER_AD_* + NAVER_CLIENT_*.
★읽기 전용. 조견표/주문가공기 시트에 절대 쓰지 않는다.
"""

from __future__ import annotations

import os
import sys
import time

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
    from dotenv import dotenv_values, load_dotenv
    load_dotenv()
    # st.secrets 접근이 .env 네이버 키를 덮어쓰는 오염을 .env 직접전달로 우회(1단계 함정).
    _ENV = {k: v for k, v in dotenv_values(os.path.join(_ROOT, ".env")).items() if v}
except ImportError:
    _ENV = {}

import config
from src.adapters.naver_adapter import NaverAdapter
from src.core.jogyeonpyo import (
    build_keyword,
    harvest_jogyeonpyo_kw_items,
    normalize_car_keyword,
    read_car_models,
)
from src.core.teamp_mode import (
    BlogFetchError,
    fetch_blog_count,
    fetch_teamp_kw_rows_partial,
)

# 신차 포함 확인용(꼬리물기로 못 잡던 차종). 대상 밖이면 별도로 덧붙여 '잡히는지'만 확인.
NEWCAR_TOKENS = ["콜레오스", "액티언", "토레스", "그랑콜레오스"]


def main() -> None:
    sheet_id = sys.argv[1] if len(sys.argv) > 1 else None
    if len(sys.argv) > 2 and sys.argv[2].isdigit():
        limit = int(sys.argv[2])
    else:
        limit = config.JOGYEONPYO_TEST_LIMIT   # None = 전체
    limit_desc = f"앞 {limit}개" if limit else "탭 전체"

    WORKSHEET = sys.argv[3] if len(sys.argv) > 3 else "에어컨필터"
    PRODUCT = next(
        (c["product_kw"] for c in config.JOGYEONPYO_PRODUCTS.values()
         if c["worksheet"] == WORKSHEET),
        WORKSHEET,
    )

    print("=" * 72)
    print(f"조견표 모드 검증 — {WORKSHEET} 탭 (제품 '{PRODUCT}'), {limit_desc}")
    print("=" * 72)

    # ① 조견표 읽기 (캐시 대상과 동일 경로)
    all_models = read_car_models(worksheet=WORKSHEET, sheet_id=sheet_id)
    models = read_car_models(worksheet=WORKSHEET, sheet_id=sheet_id, limit=limit)
    print(f"[①] 차종 총 {len(all_models)}개 → 검증 대상 {limit_desc} {len(models)}개")

    # 신차가 대상 안에 있나? 없으면 별도 덧붙여 '잡히는지'만 추가 확인.
    def _is_newcar(name: str) -> bool:
        return any(tok in normalize_car_keyword(name) for tok in NEWCAR_TOKENS)

    in_batch = [m for m in models if _is_newcar(m)]
    extra_new = [m for m in all_models if _is_newcar(m) and m not in models]
    print(f"     대상 중 신차: {in_batch or '없음'}")
    if extra_new:
        print(f"     (참고) 대상 밖 신차 {extra_new} → 별도 덧붙여 잡히는지 확인")

    targets = models + extra_new

    # ② 검색량 라이브 조회 (UI 와 동일: harvest_jogyeonpyo_kw_items)
    adapter = NaverAdapter(
        [PRODUCT],
        api_key=_ENV.get("NAVER_AD_API_KEY"),
        secret_key=_ENV.get("NAVER_AD_SECRET_KEY"),
        customer_id=_ENV.get("NAVER_AD_CUSTOMER_ID"),
    )

    done_box = {"n": 0}

    def _vcb(done: int, total: int) -> None:
        done_box["n"] = done
        if done == total or done % 10 == 0:
            print(f"     검색량 진행 {done}/{total}")

    t0 = time.perf_counter()
    print(f"\n[②] 검색량 조회 시작 (차종 {len(targets)}개, rate_limit={adapter.rate_limit_seconds}s)...")
    kw_items, jp_failed = harvest_jogyeonpyo_kw_items(adapter, targets, PRODUCT, on_progress=_vcb)
    t_vol = time.perf_counter() - t0
    print(f"     검색량 조회 끝: {t_vol:.1f}s · 검색량>0 {len(kw_items)}개 · 실패(429) {len(jp_failed)}건")

    # ③ 블로그 문서수·비율 (UI 와 동일 경로)
    cid = _ENV.get("NAVER_CLIENT_ID") or os.environ.get("NAVER_CLIENT_ID")
    csec = _ENV.get("NAVER_CLIENT_SECRET") or os.environ.get("NAVER_CLIENT_SECRET")
    blog_429 = {"n": 0}

    def blog_fn(query: str) -> int:
        try:
            return fetch_blog_count(query, cid, csec)
        except BlogFetchError as e:
            if "429" in str(e):
                blog_429["n"] += 1
            raise

    t1 = time.perf_counter()
    print(f"\n[③] 블로그 문서수 조회 (병렬 {config.NAVER_BLOG_MAX_WORKERS})...")
    rows, blog_failed = fetch_teamp_kw_rows_partial(
        kw_items, blog_fn, max_workers=config.NAVER_BLOG_MAX_WORKERS,
    )
    t_blog = time.perf_counter() - t1

    newcar_keys = {build_keyword(m, PRODUCT) for m in (in_batch + extra_new)}
    print(f"\n  {'키워드':<26} {'검색량':>7} {'문서수':>8} {'비율':>7}  등급")
    print("  " + "-" * 64)
    for r in rows[:30]:
        mark = "  ← 신차" if r.keyword in newcar_keys else ""
        print(f"  {r.keyword:<26} {r.volume:>7} {r.doc_count:>8} {r.ratio:>7.2f}  {r.grade}{mark}")
    if len(rows) > 30:
        print(f"  ... (상위 30개만 표시, 총 {len(rows)}개)")

    newcar_hit = [r.keyword for r in rows if r.keyword in newcar_keys]
    gold = sum(1 for r in rows if r.grade == "🟡 황금")
    okk = sum(1 for r in rows if r.grade == "🟢 해볼만")
    sat = sum(1 for r in rows if r.grade == "🔴 포화/후순위")
    gold_kws = [r.keyword for r in rows if r.grade == "🟡 황금"]

    print("\n" + "=" * 72)
    print("결과 요약")
    print(f"  · 차종 {len(all_models)}개 중 검증 {len(targets)}개 ({limit_desc} + 신차덧붙임 {len(extra_new)})")
    print(f"  · 검색량 조회: {t_vol:.1f}s, 검색량>0 {len(kw_items)}개, 429 {len(jp_failed)}건")
    print(f"  · 블로그 조회: {t_blog:.1f}s, 성공 {len(rows)}개, 실패 {len(blog_failed)}건(429 {blog_429['n']})")
    print(f"  · 총 소요: {t_vol + t_blog:.1f}s ({(t_vol + t_blog)/60:.1f}분)")
    print(f"  · 총 429: {len(jp_failed) + blog_429['n']}건")
    print(f"  · 등급: 🟡황금 {gold} / 🟢해볼만 {okk} / 🔴포화 {sat}")
    print(f"  · 황금 키워드: {gold_kws}")
    print(f"  · 신차 잡힘: {newcar_hit or '(검색량 미형성으로 제외됐을 수 있음)'}")
    print("=" * 72)


if __name__ == "__main__":
    main()
