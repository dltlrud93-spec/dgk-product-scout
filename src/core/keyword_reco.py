"""
keyword_reco.py — 체험단 양식용 가벼운 키워드 추천(연관 키워드 + 월검색량).

검색광고 키워드도구의 연관 키워드를 받아 '키워드 + 월검색량(PC+모바일 합산)'만
돌려준다. ★비율·블로그문서수·최신성 같은 무거운 계산은 하지 않는다(양식 화면은 가벼워야 함).

수집 코어는 search_volume 의 검증된 헬퍼(dedupe_relkeywords/member_volume)를 그대로 재사용.
호출 경로는 fetch_aggregated_volume 과 동일하게 request_fn(테스트) 또는 adapter 둘 중 하나.
"""

from __future__ import annotations

import re
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


# ── 블로그 제목 기반 키워드 추천 ──────────────────────────────────────────────
# 검색광고 연관어는 검색량 쌓인 키워드만 반환 → 신차(EV5 등)는 빈약(연관어 1개).
# 네이버 블로그 검색은 검색량 무관하게 실제 글 제목을 주므로, 제목들을 빈도 기반으로
# 토큰화해 키워드 후보를 보완한다. ★형태소분석 없음 — 빈도+불용어로 시작하는 근사.

# 토큰: 한글/영문/숫자 연속(특수문자·공백이 경계). 1글자·숫자만은 추출 단계에서 제외.
_TITLE_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")

# 불용어: 조사·일반어·블로그 상투어. 키워드 가치가 없는 단어만(교체·냄새·후기·셀프 등
# 의미 있는 키워드는 ★남긴다). 노이즈를 줄이는 출발점 — 운영하며 보강.
_BASE_BLOG_STOPWORDS = frozenset({
    # 블로그 상투어·정리어
    "후기는", "리뷰", "내돈내산", "솔직", "찐", "리얼", "총정리", "정리", "기록",
    "일상", "데일리", "블로그", "네이버", "스토리", "이야기", "꿀팁", "추천", "공유",
    "정보", "소개", "모음", "가이드", "feat", "vlog", "brunch",
    # 동사·서술 상투어(제목 말꼬리)
    "해봤어요", "해봤습니다", "했어요", "했습니다", "알아보기", "알아봤어요",
    "알아봅시다", "입니다", "이에요", "예요", "네요", "어요", "봤어요", "봤습니다",
    "하는법", "하는방법", "하기", "되는", "하는", "위한", "있는", "없는", "같은",
    "이런", "저런", "그런", "이렇게", "저렇게", "그냥", "직접",
    # 일반 부사·연결어
    "정도", "진짜", "완전", "너무", "아주", "매우", "그리고", "또는", "하지만",
    "그래서", "근데", "그리고요", "어떻게", "무엇", "어디", "언제", "왜",
})

# 차량 일반어·구매/스펙어·브랜드(제조사) — 에어컨필터·와이퍼와 ★무관한 노이즈.
# 신차("EV5") 블로그 검색이 일반 차량 글을 섞어 반환할 때 따라오는 단어들.
# ★제품 관련어(활성탄·헤파·냄새·사이즈·발수 등)는 ★절대 넣지 않는다(유지돼야 함).
# "하이브리드"는 와이퍼 제품 타입이기도 해서(제품 관련어) 일부러 제외하지 않는다.
# 매칭은 토큰 ★완전일치(소문자)라 영문은 소문자로 적는다.
VEHICLE_STOPWORDS = frozenset({
    # 차량 일반·구매/스펙어
    "보조금", "연비", "유지비", "풀체인지", "주행거리", "제원표", "모의견적",
    "신형", "전기차", "패밀리", "스탠다드", "가솔린", "디젤", "트림", "출고",
    "계약", "견적", "할부", "리스", "보험", "gt",
    # 브랜드·제조사(차종명 일반)
    "기아", "현대", "제네시스", "쉐보레", "르노", "쌍용",
})

BLOG_STOPWORDS = _BASE_BLOG_STOPWORDS | VEHICLE_STOPWORDS


# 제품 동의어 — 블로그 제목이 '제품 관련 글'인지 판정 + 검색어에서 제품 식별용.
# key 는 대표 제품군, value 는 제목/검색어에 포함되면 그 제품으로 보는 단어들.
# ★부분 문자열 매칭(정규화 후) — "에어컨 필터"는 정규화 시 "에어컨필터"와 같아진다.
PRODUCT_SYNONYMS = {
    "에어컨필터": [
        "에어컨필터", "에어컨 필터", "캐빈필터", "에어필터", "에어콘필터",
        "향균필터", "항균필터", "공조필터", "필터",
    ],
    "와이퍼": [
        "와이퍼", "와이퍼블레이드", "블레이드", "윈도우브러시",
    ],
}


def _norm(text: str) -> str:
    """소문자화 + 모든 공백 제거 — 대소문자·띄어쓰기 무관 매칭용."""
    return re.sub(r"\s+", "", (text or "")).lower()


# 제품군별 정규화 동의어 집합(미리 계산).
_PRODUCT_SYN_NORM = {
    fam: frozenset(_norm(w) for w in syns) for fam, syns in PRODUCT_SYNONYMS.items()
}


def detect_product_synonyms(seed: str) -> Optional[frozenset]:
    """검색어에서 제품을 식별해 그 제품의 ★정규화 동의어 집합을 돌려준다.

    예: "EV5 에어컨필터" → 에어컨필터 동의어({에어컨필터, 캐빈필터, 필터, ...}).
    여러 제품군이 걸리면 합집합. 알려진 제품어가 없으면(차종명만 등) None
    → 호출부는 제목 필터를 적용하지 않는다(기존 동작 유지).
    """
    s = _norm(seed)
    if not s:
        return None
    matched: set[str] = set()
    for syns in _PRODUCT_SYN_NORM.values():
        if any(w in s for w in syns):
            matched |= syns
    return frozenset(matched) if matched else None


def _title_has_product(title: str, product_terms) -> bool:
    """제목(정규화)에 제품 동의어가 하나라도 포함되는지."""
    t = _norm(title)
    return any(w in t for w in product_terms)


def extract_title_keywords(
    titles: list[str],
    seed: str,
    *,
    limit: int = 20,
    stopwords=BLOG_STOPWORDS,
    product_terms=None,
) -> list[tuple[str, int]]:
    """블로그 글 제목들에서 키워드 후보를 빈도 기반으로 추출한다(형태소분석 없음).

    반환: [(키워드, 등장 제목 수), ...]. 등장 제목 수 내림차순(동률은 첫 등장 순).
    제외: ①검색어 토큰(과 그 결합형) ②불용어(차량 일반어 포함) ③1글자 ④숫자만.
    빈도는 ★제목 단위 1회(document frequency).

    ★제품 관련성 필터: product_terms 가 주어지거나 검색어에서 제품을 식별하면,
    제품 동의어가 든 제목에서만 키워드를 뽑는다(차량 일반 글의 보조금·연비 등 차단).
    제품 식별이 안 되면 필터하지 않는다(기존 동작 유지).
    """
    if product_terms is None:
        product_terms = detect_product_synonyms(seed)
    if product_terms:
        titles = [t for t in titles if _title_has_product(t, product_terms)]

    seed_tok_list = [t.lower() for t in _TITLE_TOKEN_RE.findall(seed or "")]
    # 검색어 자체("EV5 에어컨필터")와 공백 제거 결합형("ev5에어컨필터")도 제외 대상.
    # ★결합형은 검색어 등장 순서 그대로 이어붙인다(set join 은 순서 비결정적 → 매칭 실패).
    seed_forms = set(seed_tok_list)
    if seed_tok_list:
        seed_forms.add("".join(seed_tok_list))

    counts: dict[str, int] = {}
    display: dict[str, str] = {}   # 소문자 키 → 첫 등장 표기(영문 대소문자 보존)
    order: dict[str, int] = {}     # 소문자 키 → 첫 등장 순서(동률 tie-break)

    for idx, title in enumerate(titles):
        seen: set[str] = set()     # 이 제목에서 이미 센 토큰(제목 단위 1회)
        for tok in _TITLE_TOKEN_RE.findall(title or ""):
            low = tok.lower()
            if low in seen:
                continue
            if len(tok) < 2 or tok.isdigit():
                continue
            if low in seed_forms or low in stopwords:
                continue
            seen.add(low)
            if low not in counts:
                counts[low] = 0
                display[low] = tok
                order[low] = idx
            counts[low] += 1

    items = sorted(counts.items(), key=lambda kv: (-kv[1], order[kv[0]]))
    out = [(display[low], cnt) for low, cnt in items]
    return out[:limit] if limit else out


def recommend_blog_keywords(
    seed: str,
    *,
    titles_fn: Optional[Callable[[str], list[str]]] = None,
    titles: Optional[list[str]] = None,
    limit: int = 20,
) -> dict:
    """검색어로 블로그 제목을 받아 키워드 후보를 추출 + 금지어 분리.

    titles 를 직접 주면 그걸 쓰고(테스트), 없으면 titles_fn(seed) 로 가져온다(라이브).
    반환: {"keywords": [(kw,count),...], "titles": [원문...], "excluded": [금지어...]}.
    빈 검색어는 빈 결과. titles_fn 예외는 호출부로 전파(블로그 실패 시 연관어만 살리기 위함).
    """
    seed = (seed or "").strip()
    if not seed:
        return {"keywords": [], "titles": [], "excluded": []}

    if titles is None:
        titles = titles_fn(seed) if titles_fn is not None else []
    title_list = list(titles)

    # 제품 관련성: 제품을 식별하면 그 제품이 든 제목만 키워드 추출 대상(나머지 제외).
    product_terms = detect_product_synonyms(seed)
    n_total = len(title_list)
    n_product = (
        sum(1 for t in title_list if _title_has_product(t, product_terms))
        if product_terms else n_total
    )

    pairs = extract_title_keywords(title_list, seed, limit=limit)
    clean, excluded = partition_banned(pairs)   # 금지어 제외(단일 출처 재사용)
    # titles 는 ★원문 전체 보존(시경이 맥락 참고용으로 모두 볼 수 있게).
    return {
        "keywords": clean,
        "titles": title_list,
        "excluded": excluded,
        "n_product_titles": n_product,
        "n_total_titles": n_total,
    }
