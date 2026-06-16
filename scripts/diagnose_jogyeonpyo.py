"""
diagnose_jogyeonpyo.py — 조견표 연동 1단계 소량 검증 (막힘 방지, 앞 20개 + 신차 강제포함).

검증 항목(지시서 1단계):
  ① 조견표 읽기 OK  — 차종 개수·샘플 출력
  ② 키워드 변환 OK  — "{정규화차종} 에어컨필터"
  ③ 검색량·문서수·비율 조회 OK — 기존 체험단 로직 재사용(병렬3·지연·백오프)
  ④ 429 발생 여부   — 실패(재시도 소진) 건수 집계

실행:
  python scripts/diagnose_jogyeonpyo.py <SHEET_ID>
  또는 환경변수 JOGYEONPYO_SHEET_ID 설정 후 인자 없이.

필요 키:
  · 구글시트: st.secrets['gcp_service_account'] 또는 로컬 service_account.json (주문가공기 것 폴백).
  · 검색량 : NAVER_AD_API_KEY / NAVER_AD_SECRET_KEY / NAVER_AD_CUSTOMER_ID (.env)
  · 문서수 : NAVER_CLIENT_ID / NAVER_CLIENT_SECRET (.env)

★읽기 전용. 조견표/주문가공기 시트에 절대 쓰지 않는다. 전체 221개 X — 소량만.
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
    from dotenv import dotenv_values, load_dotenv
    load_dotenv()
    # ★ st.secrets 접근 시 Streamlit 이 .streamlit/secrets.toml 의 (로컬 플레이스홀더) 네이버
    #   키를 os.environ 에 export 해 .env 실제 키를 덮어쓴다. 조견표를 읽으면 st.secrets 가
    #   닿으므로, 진단은 .env 원본값을 직접 읽어 어댑터·블로그 호출에 '명시 전달'한다(오염 면역).
    _ENV = {k: v for k, v in dotenv_values(os.path.join(_ROOT, ".env")).items() if v}
except ImportError:
    _ENV = {}

import config
from src.adapters.naver_adapter import NaverAdapter
from src.core.car_models import normalize_text
from src.core.jogyeonpyo import build_keyword, normalize_car_keyword, read_car_models
from src.core.search_volume import member_volume
from src.core.teamp_mode import (
    BlogFetchError,
    fetch_blog_count,
    fetch_teamp_kw_rows_partial,
)

PRODUCT = "에어컨필터"
SAMPLE_N = 20
# 핵심 검증: 연관어 꼬리물기로 못 잡던 신차가 조견표→키워드로 잡히는지.
#   조견표 차종명에 이 토큰이 들어간 차종은 20개 밖이어도 강제 포함.
MUST_INCLUDE_TOKENS = ["콜레오스", "액티언", "토레스", "그랑콜레오스"]


def _select_models(all_models: list[str]) -> tuple[list[str], list[str]]:
    """앞 SAMPLE_N개 + 신차(MUST_INCLUDE) 강제 포함(중복 없이, 순서 유지). (선택분, 강제분) 반환."""
    selected = list(all_models[:SAMPLE_N])
    seen = set(selected)
    forced: list[str] = []
    for m in all_models:
        if m in seen:
            continue
        if any(tok in normalize_car_keyword(m) for tok in MUST_INCLUDE_TOKENS):
            forced.append(m)
            seen.add(m)
    return selected, forced


def _keyword_volume(adapter: NaverAdapter, keyword: str) -> int:
    """생성 키워드의 검색량 = 그 키워드 자체 행(monthlyPc+Mobile). 매칭 행 없으면 0(수요 없음/신차)."""
    rows = adapter._request_keywordstool([keyword])
    target = normalize_text(keyword)   # 공백 제거 + 대문자화 (네이버 relKeyword 정규화와 동일)
    for row in rows:
        if normalize_text(str(row.get("relKeyword", ""))) == target:
            return member_volume(row)
    return 0


def main() -> None:
    sheet_id = sys.argv[1] if len(sys.argv) > 1 else None

    print("=" * 70)
    print("조견표 연동 1단계 소량 검증 — 에어컨필터 탭")
    print("=" * 70)

    # ── ① 조견표 읽기 ──────────────────────────────────────────────
    print("\n[①] 조견표 읽기...")
    all_models = read_car_models(worksheet="에어컨필터", sheet_id=sheet_id)
    print(f"  · 차종 총 개수: {len(all_models)}")
    print(f"  · 샘플(앞 10): {all_models[:10]}")

    selected, forced = _select_models(all_models)
    targets = selected + forced
    print(f"  · 테스트 대상: 앞 {len(selected)}개 + 신차 강제포함 {len(forced)}개({forced}) = {len(targets)}개")

    # ── ② 키워드 변환 ──────────────────────────────────────────────
    print("\n[②] 키워드 변환 샘플:")
    kw_items: list[tuple[str, str, int]] = []  # (keyword, car_model, volume_placeholder)
    for m in targets:
        kw = build_keyword(m, PRODUCT)
        if not kw:
            continue
        print(f"  · {m!r:>16} → {kw!r}")

    # ── ③ 검색량 조회 (키워드별) ──────────────────────────────────
    print("\n[③] 검색량 조회(키워드별, 검색광고 키워드도구)...")
    adapter = NaverAdapter(
        [PRODUCT],
        api_key=_ENV.get("NAVER_AD_API_KEY"),
        secret_key=_ENV.get("NAVER_AD_SECRET_KEY"),
        customer_id=_ENV.get("NAVER_AD_CUSTOMER_ID"),
    )  # .env 원본 키 명시 전달(st.secrets 오염 우회). 호출은 키워드별 _request_keywordstool.
    valid_items: list[tuple[str, str, int]] = []
    for i, m in enumerate(targets):
        kw = build_keyword(m, PRODUCT)
        if not kw:
            continue
        if i > 0:
            adapter._sleep(adapter.rate_limit_seconds)
        try:
            vol = _keyword_volume(adapter, kw)
        except Exception as e:  # noqa: BLE001
            print(f"    검색량 실패 {kw!r}: {e}")
            vol = 0
        marker = "  ← 신차" if m in forced else ""
        print(f"  · {kw!r:>26}  vol={vol}{marker}")
        if vol > 0:
            valid_items.append((kw, m, vol))

    print(f"\n  검색량>0 키워드: {len(valid_items)} / {len(targets)} "
          f"(vol=0 은 블로그 조회 생략 — 신차는 아직 검색량 미형성일 수 있음)")

    # ── ④ 문서수·비율 조회 + 429 집계 ─────────────────────────────
    print("\n[④] 블로그 문서수·비율 조회(병렬3·지연·백오프 — 체험단 로직 재사용)...")
    client_id = _ENV.get("NAVER_CLIENT_ID") or os.environ.get("NAVER_CLIENT_ID")
    client_secret = _ENV.get("NAVER_CLIENT_SECRET") or os.environ.get("NAVER_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("  ! NAVER_CLIENT_ID/SECRET 미설정 — 블로그 조회 건너뜀.")
        return

    err_count = {"429": 0, "other": 0}

    def blog_fn(query: str) -> int:
        try:
            return fetch_blog_count(query, client_id, client_secret)
        except BlogFetchError as e:
            if "429" in str(e):
                err_count["429"] += 1
            else:
                err_count["other"] += 1
            raise

    rows, failed = fetch_teamp_kw_rows_partial(
        valid_items, blog_fn, max_workers=config.NAVER_BLOG_MAX_WORKERS,
    )

    print(f"\n  {'키워드':<28} {'검색량':>7} {'문서수':>8} {'비율':>7}  등급")
    print("  " + "-" * 66)
    for r in rows:
        mark = "  ← 신차" if r.car_model in forced else ""
        print(f"  {r.keyword:<28} {r.volume:>7} {r.doc_count:>8} {r.ratio:>7.2f}  {r.grade}{mark}")

    print("\n" + "=" * 70)
    print(f"결과 요약: 차종 {len(all_models)}개 | 테스트 {len(targets)}개 | "
          f"검색량>0 {len(valid_items)}개 | 조회성공 {len(rows)}개 | 실패 {len(failed)}개")
    print(f"429 재시도소진: {err_count['429']}건 | 기타실패: {err_count['other']}건")
    forced_hit = [r.keyword for r in rows if r.car_model in forced]
    print(f"신차 잡힘: {forced_hit if forced_hit else '(검색량 미형성 — vol=0 으로 제외됐을 수 있음)'}")
    print("=" * 70)


if __name__ == "__main__":
    main()
