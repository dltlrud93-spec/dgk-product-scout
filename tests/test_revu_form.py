"""
test_revu_form.py — 레뷰 체험단 양식 docx 생성 회귀 테스트.

빈 양식(templates/revu_basic_template.docx)을 로드해 값을 채운 뒤, 결과 docx 를
다시 열어 ① 올바른 표 셀에 값이 들어갔는지 ② 사전안내문(표12)과 표 구조가
100% 보존됐는지 ③ 빈 값은 빈칸으로 남는지 ④ 미션 라우팅·금지어·파일명을 검증한다.
네트워크/Streamlit 없이 순수 함수만 다룬다.
"""

from __future__ import annotations

import io

import pytest
from docx import Document

from src.revu_form import (
    SUBTITLE_MAX,
    TEMPLATE_PATH,
    TITLE_MAX,
    RevuFormData,
    assemble_tracking_url,
    build_revu_docx,
    build_tracking_url,
    default_mission_lines,
    find_banned_words,
    merge_keywords,
    suggest_filename,
)
from src.core.keyword_reco import partition_banned, recommend_keywords

pytestmark = pytest.mark.skipif(
    not TEMPLATE_PATH.exists(),
    reason="빈 양식 템플릿(templates/revu_basic_template.docx)이 없으면 건너뜀",
)


def _build(**kw):
    """입력값으로 docx 를 만들고 다시 연 Document 를 반환."""
    data = RevuFormData(**kw)
    raw = build_revu_docx(data)
    return Document(io.BytesIO(raw))


def _cell(doc, t, r):
    return doc.tables[t].rows[r].cells[0].text


# ── 표 구조·사전안내문 보존 ──────────────────────────────────────────────────

def test_template_structure_unchanged():
    """생성 docx 의 표 개수·표12 행수가 원본과 동일해야 한다."""
    doc = _build(product_name="테스트")
    assert len(doc.tables) == 13
    assert len(doc.tables[12].rows) == 29


def test_pre_notice_table_byte_identical():
    """표12(사전안내문)의 모든 셀 텍스트가 원본과 글자 단위로 동일해야 한다(불변)."""
    original = Document(str(TEMPLATE_PATH))
    filled = _build(
        product_name="에어컨필터",
        title_keywords="키워드",
        campaign_title="제목",
    )
    orig_t12 = original.tables[12]
    fill_t12 = filled.tables[12]
    assert len(orig_t12.rows) == len(fill_t12.rows)
    for ri in range(len(orig_t12.rows)):
        o = [c.text for c in orig_t12.rows[ri].cells]
        f = [c.text for c in fill_t12.rows[ri].cells]
        assert o == f, f"표12 r{ri} 사전안내문이 변경됨"


# ── 셀별 값 배치 ─────────────────────────────────────────────────────────────

def test_values_land_in_correct_cells():
    doc = _build(
        content_type="블로그",
        campaign_title="EV5 에어컨필터",
        campaign_subtitle="여름철 차량 공기질 관리",
        product_name="파이널 에어컨필터",
        provide_qty="EV5 전용 P17 2개",
        recruit_count=15,
        title_keywords="에어컨필터교체",
        body_keywords="캐빈필터, 활성탄필터",
        product_url="https://brand.naver.com/dgk/products/1",
        missions=["미션1", "미션2", "미션3"],
        manager_name="박민우",
        manager_phone="010-3924-1155",
        manager_email="dgkorea93@naver.com",
    )
    assert "블로그" in _cell(doc, 0, 1)
    assert _cell(doc, 3, 0) == "성함: 박민우\n연락처: 010-3924-1155\n이메일: dgkorea93@naver.com"
    assert _cell(doc, 4, 0) == "[ EV5 에어컨필터 ]"
    assert _cell(doc, 5, 0) == "[ 여름철 차량 공기질 관리 ]"
    assert _cell(doc, 6, 0) == "15명"
    assert "제품명: 파이널 에어컨필터" in _cell(doc, 7, 0)
    assert "제공수량: EV5 전용 P17 2개" in _cell(doc, 7, 0)
    assert _cell(doc, 8, 1) == "1. 미션1\n2. 미션2\n3. 미션3"
    assert "제목키워드 (1~3개) : 에어컨필터교체" in _cell(doc, 10, 1)
    assert "본문키워드 (3~5개) : 캐빈필터, 활성탄필터" in _cell(doc, 10, 1)
    assert _cell(doc, 11, 0) == "링크입력: https://brand.naver.com/dgk/products/1"


def test_provide_qty_no_duplicate_gae():
    """제공수량은 입력값에 단위("2개")까지 포함 → 원본 "개"가 또 붙어 "...2개 개"가
    되면 안 된다(입력값 그대로, 끝이 "2개")."""
    doc = _build(provide_qty="EV5 에어컨필터 P17 2개")
    line = [p for p in _cell(doc, 7, 0).split("\n") if p.startswith("제공수량")][0]
    assert line == "제공수량: EV5 에어컨필터 P17 2개"
    assert "2개 개" not in line
    assert "개 개" not in _cell(doc, 7, 0)


def test_clip_missions_route_to_clip_row():
    """콘텐츠=클립이면 미션은 표8 r3(클립)에, 블로그 행(r1)은 비어 있어야 한다."""
    doc = _build(content_type="클립", missions=["가", "나", "다"])
    assert _cell(doc, 8, 3).startswith("1. 가\n2. 나\n3. 다")
    assert _cell(doc, 8, 1) == "1. \n2. \n3. "


def test_blog_missions_leave_clip_row_untouched():
    """콘텐츠=블로그면 클립 미션 행(r3)의 상품ID/해시태그 블록이 보존돼야 한다."""
    doc = _build(content_type="블로그", missions=["가", "나", "다"])
    clip = _cell(doc, 8, 3)
    assert clip.startswith("1. \n2. \n3. ")
    assert "상품ID" in clip and "해시태그" in clip


def test_empty_values_leave_blanks():
    """비운 항목은 양식 빈칸 그대로(없는 값을 만들어 넣지 않음)."""
    doc = _build()  # 전부 기본/빈값
    assert _cell(doc, 4, 0).strip() == "[      ]"   # 제목 빈칸 유지
    assert _cell(doc, 5, 0).strip() == "[      ]"   # 부제목 빈칸 유지
    assert _cell(doc, 11, 0).strip() == "링크입력:"  # 링크 라벨만
    # 제품명/제공수량 라벨만 남음
    assert "제품명:" in _cell(doc, 7, 0)


# ── 보조 함수 ────────────────────────────────────────────────────────────────

def test_char_limits():
    assert TITLE_MAX == 20
    assert SUBTITLE_MAX == 40


def test_find_banned_words_detects_disease_and_absolute():
    hits = find_banned_words("이 제품은 비염에 최고입니다")
    assert "비염" in hits
    assert "최고" in hits


def test_find_banned_words_clean_returns_empty():
    assert find_banned_words("에어컨필터 교체 후기", "캐빈필터 활성탄") == []


def test_default_mission_lines_with_car_model():
    lines = default_mission_lines("EV5", "에어컨필터")
    assert len(lines) == 3
    assert "EV5" in lines[0]
    assert "에어컨필터" in lines[0]


def test_default_mission_lines_without_car_model_blank():
    assert default_mission_lines("", "에어컨필터") == ["", "", ""]


def test_suggest_filename():
    data = RevuFormData(product_name="파이널 에어컨필터", car_model="EV5")
    assert suggest_filename(data) == "레뷰_파이널 에어컨필터_EV5.docx"


def test_suggest_filename_no_car_model():
    data = RevuFormData(product_name="와이퍼")
    assert suggest_filename(data) == "레뷰_와이퍼.docx"


# ── 네이버 유입 추적 URL(nt_) ────────────────────────────────────────────────

def test_tracking_no_question_mark_starts_with_question():
    """① "?" 없는 URL → "?"로 시작."""
    url = build_tracking_url("https://m.site.naver.com/2aAvQ", "naver.blog", "social")
    assert url == "https://m.site.naver.com/2aAvQ?nt_source=naver.blog&nt_medium=social"
    assert url.count("?") == 1


def test_tracking_existing_question_mark_uses_ampersand():
    """② 이미 "?" 있는 URL → "&"로 이어붙임("?" 중복 안 됨)."""
    url = build_tracking_url("https://store.naver.com/p?foo=1", "naver.blog", "social")
    assert url == "https://store.naver.com/p?foo=1&nt_source=naver.blog&nt_medium=social"
    assert url.count("?") == 1  # ★"?" 중복 방지


def test_tracking_korean_in_source_blocked():
    """③ nt_source 에 한글 → 경고 + 생성 차단(url None)."""
    url, errors = assemble_tracking_url(
        "https://m.site.naver.com/2aAvQ", "네이버블로그", "social")
    assert url is None
    assert any("한글" in e for e in errors)


def test_tracking_space_blocked():
    """④ 값에 공백 → 경고."""
    url, errors = assemble_tracking_url(
        "https://m.site.naver.com/2aAvQ", "naver blog", "social")
    assert url is None
    assert any("공백" in e for e in errors)


def test_tracking_required_empty_blocked():
    """⑤ 필수값(nt_source/nt_medium) 비면 차단."""
    url, errors = assemble_tracking_url(
        "https://m.site.naver.com/2aAvQ", "", "social")
    assert url is None
    assert any("nt_source" in e and "필수" in e for e in errors)


def test_tracking_optional_empty_excluded_required_kept():
    """⑥ 선택값 비면 그 파라미터 제외, 필수 2개는 유지."""
    url, errors = assemble_tracking_url(
        "https://m.site.naver.com/2aAvQ", "naver.blog", "social",
        nt_detail="", nt_keyword="")
    assert errors == []
    assert "nt_source=naver.blog" in url
    assert "nt_medium=social" in url
    assert "nt_detail" not in url
    assert "nt_keyword" not in url


def test_tracking_optional_present_included_with_korean():
    """선택값(한글 허용)이 있으면 포함된다 — nt_keyword 한글 OK."""
    url, errors = assemble_tracking_url(
        "https://m.site.naver.com/2aAvQ", "naver.blog", "social",
        nt_detail="revu", nt_keyword="EV5에어컨필터")
    assert errors == []
    assert "nt_detail=revu" in url
    assert "nt_keyword=EV5에어컨필터" in url


def test_tracking_disallowed_special_char_blocked():
    """③ 보강: 허용 외 특수문자(/,=,# 등)가 값에 있으면 차단."""
    url, errors = assemble_tracking_url(
        "https://m.site.naver.com/2aAvQ", "naver.blog", "soc=ial")
    assert url is None
    assert any("특수문자" in e for e in errors)


def test_tracking_product_url_empty_blocked():
    """제품 URL 자체가 비면 차단."""
    url, errors = assemble_tracking_url("", "naver.blog", "social")
    assert url is None
    assert any("제품 URL" in e and "필수" in e for e in errors)


# ── 키워드 추천(연관키워드 + 검색량) ─────────────────────────────────────────

def _fake_keywordstool(seed):
    """검색광고 키워드도구 응답 대역 — relKeyword + 월검색량 필드."""
    return [
        {"relKeyword": "EV5 에어컨필터 교체", "monthlyPcQcCnt": 500, "monthlyMobileQcCnt": 700},
        {"relKeyword": "EV5 에어컨필터 냄새", "monthlyPcQcCnt": 300, "monthlyMobileQcCnt": 500},
        {"relKeyword": "EV5 에어컨필터", "monthlyPcQcCnt": 100, "monthlyMobileQcCnt": 50},
        # 검색량 0(원래 '<10') → 제외돼야 함
        {"relKeyword": "극소키워드", "monthlyPcQcCnt": "< 10", "monthlyMobileQcCnt": 0},
        # 중복 relKeyword → dedupe
        {"relKeyword": "EV5 에어컨필터 교체", "monthlyPcQcCnt": 500, "monthlyMobileQcCnt": 700},
    ]


def test_recommend_keywords_returns_keyword_and_volume_sorted():
    """연관키워드+검색량 수집, 검색량 내림차순, 0·중복 제외."""
    out = recommend_keywords("EV5 에어컨필터", request_fn=_fake_keywordstool)
    assert out == [
        ("EV5 에어컨필터 교체", 1200),
        ("EV5 에어컨필터 냄새", 800),
        ("EV5 에어컨필터", 150),
    ]
    assert "극소키워드" not in [k for k, _ in out]  # 검색량 0 제외


def test_recommend_keywords_empty_seed():
    """빈 검색어 → 빈 리스트(네트워크 호출 없음)."""
    assert recommend_keywords("", request_fn=_fake_keywordstool) == []
    assert recommend_keywords("   ", request_fn=_fake_keywordstool) == []


def test_recommend_keywords_limit():
    """limit 으로 상위 N개만."""
    out = recommend_keywords("EV5 에어컨필터", request_fn=_fake_keywordstool, limit=2)
    assert len(out) == 2
    assert out[0] == ("EV5 에어컨필터 교체", 1200)


def test_partition_banned_excludes_disease_keywords():
    """추천 키워드 중 금지어(질병명) 포함 항목 분리."""
    pairs = [("에어컨필터 교체", 1200), ("비염 완화 필터", 300), ("탈모 방지", 100)]
    clean, excluded = partition_banned(pairs)
    assert ("에어컨필터 교체", 1200) in clean
    assert "비염 완화 필터" in excluded
    assert "탈모 방지" in excluded
    assert all("비염" not in k and "탈모" not in k for k, _ in clean)


# ── 키워드 칸 덧붙이기(merge) ────────────────────────────────────────────────

def test_merge_keywords_into_empty():
    assert merge_keywords("", ["에어컨필터교체", "에어컨필터냄새"]) == "에어컨필터교체, 에어컨필터냄새"


def test_merge_keywords_appends_not_overwrite():
    """기존 값에 덧붙임(덮어쓰지 않음)."""
    assert merge_keywords("기존키워드", ["새키워드"]) == "기존키워드, 새키워드"


def test_merge_keywords_dedupes():
    """중복(대소문자 무시) 자동 제거, 순서 보존."""
    assert merge_keywords("EV5, 에어컨필터", ["에어컨필터", "ev5", "냄새"]) == "EV5, 에어컨필터, 냄새"


def test_merge_keywords_no_additions_keeps_existing():
    assert merge_keywords("a, b", []) == "a, b"
