"""
keyword_reco.py — 체험단 양식용 가벼운 키워드 추천(연관 키워드 + 월검색량).

검색광고 키워드도구의 연관 키워드를 받아 '키워드 + 월검색량(PC+모바일 합산)'만
돌려준다. ★비율·블로그문서수·최신성 같은 무거운 계산은 하지 않는다(양식 화면은 가벼워야 함).

수집 코어는 search_volume 의 검증된 헬퍼(dedupe_relkeywords/member_volume)를 그대로 재사용.
호출 경로는 fetch_aggregated_volume 과 동일하게 request_fn(테스트) 또는 adapter 둘 중 하나.
"""

from __future__ import annotations

from typing import Callable, Optional

from src.core.search_volume import dedupe_relkeywords, member_volume
from src.revu_form import find_banned_words


def recommend_keywords(
    seed: str,
    *,
    adapter=None,
    request_fn: Optional[Callable[[str], list[dict]]] = None,
    limit: int = 40,
) -> list[tuple[str, int]]:
    """검색어 1개로 연관 키워드 + 월검색량을 수집한다. 검색량 내림차순.

    반환: [(keyword, volume), ...]. volume 0(원래 '<10')·중복은 제외.
    빈 검색어는 빈 리스트.
    """
    seed = (seed or "").strip()
    if not seed:
        return []

    if request_fn is not None:
        rows = request_fn(seed)
    else:
        if adapter is None:
            from src.adapters.naver_adapter import NaverAdapter  # 지연 import(순환 방지)

            adapter = NaverAdapter([seed])
        rows = adapter._request_keywordstool([seed])

    out: list[tuple[str, int]] = []
    for kw, row in dedupe_relkeywords(rows).items():
        vol = member_volume(row)
        if vol <= 0:
            continue
        out.append((kw, vol))

    out.sort(key=lambda t: t[1], reverse=True)
    return out[:limit] if limit else out


def partition_banned(
    pairs: list[tuple[str, int]],
    banned_fn: Callable[..., list[str]] = find_banned_words,
) -> tuple[list[tuple[str, int]], list[str]]:
    """추천 키워드에서 금지어(질병명·절대표현 등) 포함 항목을 분리한다.

    반환: (clean[(kw,vol),...], excluded_keywords[str,...]).
    양식 금지어 로직(revu_form.find_banned_words)을 그대로 재사용 — 단일 출처.
    """
    clean: list[tuple[str, int]] = []
    excluded: list[str] = []
    for kw, vol in pairs:
        if banned_fn(kw):
            excluded.append(kw)
        else:
            clean.append((kw, vol))
    return clean, excluded
