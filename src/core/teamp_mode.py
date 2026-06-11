"""
teamp_mode.py — 체험단 타겟 선정 모드 코어.

제품 키워드(사용자 입력) → 차종 수확(꼬리물기) → (차종×제품) 행마다
  검색량(합산) + 블로그 문서수 + 비율 → 황금/해볼만/포화 3분류.

블로그 문서수:
  네이버 블로그 검색 API(GET https://openapi.naver.com/v1/search/blog.json)
  "{차종 표시명} {product}" 쿼리 → 응답 total 값.
  ★ total 은 근사치이고 블로그 노출 난이도(블로그 지수)와 다름 — 우선순위 신호이지 노출 보증 아님.

비율 = 문서수 ÷ 검색량. 낮을수록 기회(글이 수요보다 적음).
검색량 0(원래 '<10') 차종은 volume 필터로 먼저 제외 → 블로그 API 미호출.
분류 임계는 config.TEAMP_RATIO_GOLD / TEAMP_RATIO_OK.

429 대응: call_delay → fetch_blog_count 호출 전 지연. 429 응답은 지수백오프 재시도.
부분 실패: fetch_teamp_rows_partial 에서 항목별 예외 격리 → 실패 항목만 failed 리스트.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Optional

import requests

import config
from src.core.search_volume import dedupe_relkeywords, member_volume


class BlogFetchError(RuntimeError):
    """블로그 문서수 조회 실패 (429 재시도 소진 또는 기타 오류)."""


@dataclass
class TeampRow:
    canonical: str    # 차종 정규명 (인식 사전 기준)
    product: str      # 제품 키워드 (사용자 입력)
    volume: int       # 검색량(합산) — harvest_models 의 agg["volume"] 그대로
    doc_count: int    # 블로그 문서수 (네이버 블로그 검색 total)
    ratio: float      # doc_count / volume
    grade: str        # "🟡 황금" / "🟢 해볼만" / "🔴 포화/후순위"


def _query_name(canonical: str) -> str:
    """블로그 검색 쿼리용 차종 표시명.

    인식 사전의 정규명에 포함되는 '(세대미상)' 등 괄호 표기를 제거하고
    실제 사람들이 검색하는 차종명만 남긴다.

    예) "모닝(세대미상)" → "모닝"
        "아반떼(세대미상)" → "아반떼"
        "아반떼CN7" → "아반떼CN7"   (괄호 없으면 그대로)
        "레이" → "레이"

    ★ 이유: canonical="모닝(세대미상)"을 그대로 query에 쓰면 "모닝(세대미상) 에어컨필터"로
      검색되어 블로그 결과가 극소 (~630) → 비율 0.13(황금 오분류). 실제 검색어인
      "모닝 에어컨필터"를 쓰면 ~18,850개로 올바른 분류(포화)가 된다.
    """
    return re.sub(r"\(.*?\)", "", canonical).strip()


def classify_ratio(
    ratio: float,
    *,
    gold: float = config.TEAMP_RATIO_GOLD,
    ok: float = config.TEAMP_RATIO_OK,
) -> str:
    """비율 → 3분류.

    · ratio < gold  → 🟡 황금   (글이 수요보다 적어 노릴 자리)
    · gold ≤ ratio ≤ ok → 🟢 해볼만
    · ratio > ok    → 🔴 포화/후순위
    """
    if ratio < gold:
        return "🟡 황금"
    if ratio <= ok:
        return "🟢 해볼만"
    return "🔴 포화/후순위"


def fetch_blog_count(
    query: str,
    client_id: str,
    client_secret: str,
    *,
    http_get: Optional[Callable] = None,
    sleep_fn: Optional[Callable[[float], None]] = None,
    max_retries: int = config.NAVER_BLOG_MAX_RETRIES,
    backoff_seconds: float = config.NAVER_BLOG_BACKOFF_SECONDS,
    call_delay: float = config.NAVER_BLOG_CALL_DELAY,
) -> int:
    """네이버 블로그 검색 API → 문서수(total).

    호출 전 call_delay 초 대기(rate limit 완충).
    429 응답: Retry-After 헤더 우선, 없으면 지수백오프(1→2→4초). 최대 max_retries 회.
    재시도 소진 시 BlogFetchError 발생.

    ★ total 은 근사치(네이버 내부 추정). 블로그 지수(상위 글 품질)는 미반영.
    """
    _get = http_get if http_get is not None else requests.get
    _sleep = sleep_fn if sleep_fn is not None else time.sleep

    _sleep(call_delay)

    for attempt in range(max_retries + 1):
        resp = _get(
            config.NAVER_BLOG_SEARCH_URL,
            params={"query": query, "display": config.NAVER_BLOG_SEARCH_DISPLAY},
            headers={
                "X-Naver-Client-Id": client_id,
                "X-Naver-Client-Secret": client_secret,
            },
            timeout=10,
        )
        if resp.status_code == 429:
            if attempt < max_retries:
                retry_after = resp.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else backoff_seconds * (2 ** attempt)
                except ValueError:
                    wait = backoff_seconds * (2 ** attempt)
                _sleep(wait)
                continue
            raise BlogFetchError(
                f"429 Too Many Requests — {max_retries}회 재시도 소진: {query!r}"
            )
        resp.raise_for_status()
        return int(resp.json().get("total", 0))

    raise BlogFetchError(f"재시도 소진: {query!r}")  # 실질적 도달 불가(루프 구조상)


def fetch_teamp_rows_partial(
    valid_items: list[tuple[str, str, int]],
    blog_fn: Callable[[str], int],
    max_workers: int = config.NAVER_BLOG_MAX_WORKERS,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> tuple[list[TeampRow], list[tuple[str, str, int]]]:
    """(canonical, ptype, volume) 리스트 → (성공 TeampRow 목록, 실패 항목 목록).

    blog_fn 이 예외를 발생시키면 해당 항목을 failed 에 수집하고 나머지는 계속 처리.
    on_progress(done, total) 콜백 — 항목 완료마다 호출(선택).
    반환된 rows 는 ratio 오름차순 정렬.
    """
    if not valid_items:
        return [], []

    total = len(valid_items)
    rows: list[TeampRow] = []
    failed: list[tuple[str, str, int]] = []
    done = 0

    with ThreadPoolExecutor(max_workers=min(max_workers, total)) as executor:
        future_map = {
            executor.submit(blog_fn, f"{_query_name(c)} {p}"): (c, p, v)
            for c, p, v in valid_items
        }
        for future in as_completed(future_map):
            c, p, v = future_map[future]
            try:
                doc_count = future.result()
                ratio = doc_count / v
                rows.append(TeampRow(c, p, v, doc_count, ratio, classify_ratio(ratio)))
            except Exception:
                failed.append((c, p, v))
            done += 1
            if on_progress:
                on_progress(done, total)

    rows.sort(key=lambda r: r.ratio)
    return rows, failed


def build_teamp_rows(
    agg: dict,
    client_id: str,
    client_secret: str,
    *,
    blog_fetch_fn: Optional[Callable[[str], int]] = None,
    max_workers: int = 1,
) -> list[TeampRow]:
    """
    harvest_models 결과(agg) → TeampRow 리스트.

    agg 구조: {(canonical, ptype): {"volume": int, "members": int, ...}}
    harvest_models 호출 시 part_seeds = {product_kw: [product_kw]} 로 구성하면
    ptype == product_keyword 가 된다.

    처리 순서:
    1. volume == 0(원래 '<10') 행 먼저 제외 — 블로그 API 미호출.
    2. 남은 행: _query_name(canonical)으로 쿼리 구성 ("모닝(세대미상)" → "모닝").
    3. max_workers > 1 이면 ThreadPoolExecutor 병렬, 아니면 순차 실행.
    4. 비율 오름차순 정렬(황금이 위).
    """
    _blog = blog_fetch_fn or (lambda q: fetch_blog_count(q, client_id, client_secret))

    # Step 1: volume=0 먼저 필터 (블로그 API 호출 전)
    valid = [
        (canonical, ptype, a["volume"])
        for (canonical, ptype), a in agg.items()
        if a["volume"] > 0
    ]

    if not valid:
        return []

    rows: list[TeampRow] = []

    if max_workers > 1:
        # 병렬 실행 (Streamlit 앱 경로는 render_teamp 에서 직접 처리 — 여기는 직접 호출용)
        with ThreadPoolExecutor(max_workers=min(max_workers, len(valid))) as executor:
            future_map = {
                executor.submit(_blog, f"{_query_name(c)} {p}"): (c, p, v)
                for c, p, v in valid
            }
            for future in as_completed(future_map):
                c, p, v = future_map[future]
                doc_count = future.result()
                ratio = doc_count / v
                rows.append(TeampRow(c, p, v, doc_count, ratio, classify_ratio(ratio)))
    else:
        for c, p, v in valid:
            doc_count = _blog(f"{_query_name(c)} {p}")
            ratio = doc_count / v
            rows.append(TeampRow(c, p, v, doc_count, ratio, classify_ratio(ratio)))

    rows.sort(key=lambda r: r.ratio)
    return rows


def top_gold_rows(rows: list[TeampRow], n: int = 10) -> list[TeampRow]:
    """🟡 황금 행 중 검색량 높은 순 상위 n개."""
    gold = [r for r in rows if r.grade == "🟡 황금"]
    gold.sort(key=lambda r: r.volume, reverse=True)
    return gold[:n]


def top_gold_rows_by_ratio(rows: list[TeampRow], n: int = 10) -> list[TeampRow]:
    """🟡 황금 행 중 비율 낮은 순 상위 n개."""
    gold = [r for r in rows if r.grade == "🟡 황금"]
    gold.sort(key=lambda r: r.ratio)
    return gold[:n]


# ═══════════════════════════════════════════════════════════════════════════════
# 키워드 단위 체험단 모드 — 합산 없음, 개별 키워드가 단위
# ═══════════════════════════════════════════════════════════════════════════════
#
# 핵심 원칙:
#   · 단위 = 개별 연관 키워드(예: "셀토스에어컨필터", "셀토스자동차에어컨필터" 각각 별도 행).
#   · 검색량 = 그 키워드의 monthlyPcQcCnt + monthlyMobileQcCnt (합산 아님).
#   · 블로그 문서수 = keyword 원문 그대로 네이버 블로그 검색 → total.
#     ★ 검색량을 잰 키워드 = 문서수를 재는 쿼리 — 100% 동일 문자열.
#   · 차종 인식은 표시 전용(car_model 컬럼) — 필터·그룹핑·합산에 사용 금지.


@dataclass
class TeampKwRow:
    keyword: str       # 연관 키워드 원문 (예: "셀토스에어컨필터")
    car_model: str     # 차종 표시용 (차종 인식 사전 → canonical. 미인식이면 "")
    volume: int        # 개별 키워드 검색량 (monthlyPcQcCnt + monthlyMobileQcCnt)
    doc_count: int     # 블로그 문서수 (keyword 원문 그대로 블로그 검색 → total)
    ratio: float       # doc_count / volume
    grade: str         # "🟡 황금" / "🟢 해볼만" / "🔴 포화/후순위"


def harvest_teamp_kw_items(
    adapter,
    products: list[str],
    index,
) -> list[tuple[str, str, int]]:
    """제품 키워드들로 연관 키워드 수확 → 제품명 포함 + volume>0 필터.

    반환: [(keyword, car_model_display, volume), ...] volume 내림차순.
    ★ index.recognize 는 표시용 car_model 컬럼만 — 필터·그룹핑 미사용.
    ★ 블로그 검색 쿼리 = keyword 원문 그대로 (차종명 정제·공백 삽입·괄호 제거 없음).
    """
    product_terms = [p.strip().lower() for p in products if p.strip()]

    def _contains_product(kw: str) -> bool:
        kw_l = kw.lower()
        return any(term in kw_l for term in product_terms)

    all_kws: dict[str, dict] = {}  # relKeyword → raw row (시드 간 dedupe)
    for i, seed in enumerate(products):
        seed = seed.strip()
        if not seed:
            continue
        if i > 0:
            adapter._sleep(adapter.rate_limit_seconds)
        rows = adapter._request_keywordstool([seed])
        for kw, row in dedupe_relkeywords(rows).items():
            if kw not in all_kws:
                all_kws[kw] = row

    result: list[tuple[str, str, int]] = []
    for kw, row in all_kws.items():
        if not _contains_product(kw):
            continue
        vol = member_volume(row)
        if vol == 0:
            continue
        rec = index.recognize(kw)
        car_model = rec.canonical if rec.recognized else ""
        result.append((kw, car_model, vol))

    result.sort(key=lambda x: x[2], reverse=True)
    return result


def fetch_teamp_kw_rows_partial(
    kw_items: list[tuple[str, str, int]],
    blog_fn: Callable[[str], int],
    max_workers: int = config.NAVER_BLOG_MAX_WORKERS,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> tuple[list[TeampKwRow], list[tuple[str, str, int]]]:
    """(keyword, car_model, volume) 리스트 → (성공 TeampKwRow 목록, 실패 항목 목록).

    ★ blog_fn 에 keyword 원문 그대로 전달 — _query_name 등 변환 없음.
    blog_fn 예외 발생 시 해당 항목 failed 수집 후 계속 처리(부분 실패 허용).
    반환된 rows 는 ratio 오름차순 정렬.
    """
    if not kw_items:
        return [], []

    total = len(kw_items)
    rows: list[TeampKwRow] = []
    failed: list[tuple[str, str, int]] = []
    done = 0

    with ThreadPoolExecutor(max_workers=min(max_workers, total)) as executor:
        future_map = {
            executor.submit(blog_fn, keyword): (keyword, car_model, volume)
            for keyword, car_model, volume in kw_items
        }
        for future in as_completed(future_map):
            keyword, car_model, volume = future_map[future]
            try:
                doc_count = future.result()
                ratio = doc_count / volume
                rows.append(TeampKwRow(keyword, car_model, volume, doc_count, ratio, classify_ratio(ratio)))
            except Exception:
                failed.append((keyword, car_model, volume))
            done += 1
            if on_progress:
                on_progress(done, total)

    rows.sort(key=lambda r: r.ratio)
    return rows, failed


def top_gold_kw_rows(rows: list[TeampKwRow], n: int = 10) -> list[TeampKwRow]:
    """🟡 황금 키워드 중 검색량 높은 순 상위 n개."""
    gold = [r for r in rows if r.grade == "🟡 황금"]
    gold.sort(key=lambda r: r.volume, reverse=True)
    return gold[:n]


def top_gold_kw_rows_by_ratio(rows: list[TeampKwRow], n: int = 10) -> list[TeampKwRow]:
    """🟡 황금 키워드 중 비율 낮은 순 상위 n개."""
    gold = [r for r in rows if r.grade == "🟡 황금"]
    gold.sort(key=lambda r: r.ratio)
    return gold[:n]
