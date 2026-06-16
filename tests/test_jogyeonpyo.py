"""
test_jogyeonpyo.py — 조견표 차종명 정규화 + 차종 목록 추출(순수 함수) 회귀 테스트.

네트워크/구글시트 접근 없이 검증 가능한 부분만 다룬다(인증·라이브 읽기는 진단 스크립트).
"""

from __future__ import annotations

from src.core.jogyeonpyo import (
    DEFAULT_CAR_COL,
    build_keyword,
    extract_models,
    harvest_jogyeonpyo_kw_items,
    keyword_volume,
    normalize_car_keyword,
)


class _FakeAdapter:
    """검색광고 키워드도구 대역 — vol_map[keyword]=(pc,mobile). fail_kws 는 예외 발생."""

    def __init__(self, vol_map: dict, fail_kws: tuple = ()):
        self.rate_limit_seconds = 0.0
        self.vol_map = vol_map
        self.fail_kws = set(fail_kws)
        self.sleeps = 0

    def _sleep(self, _s):
        self.sleeps += 1

    def _request_keywordstool(self, hints):
        kw = hints[0]
        if kw in self.fail_kws:
            raise RuntimeError("429 Too Many Requests")
        pc, mob = self.vol_map.get(kw, (0, 0))
        if pc == 0 and mob == 0:
            return []   # 매칭 행 없음 → 검색량 0
        return [{"relKeyword": kw, "monthlyPcQcCnt": pc, "monthlyMobileQcCnt": mob}]


# ── 정규화 ──────────────────────────────────────────────────────────────────

def test_normalize_removes_internal_space():
    # 핵심 검증: 신차 "그랑 콜레오스"(조견표 띄어쓰기) → "그랑콜레오스"(검색형)
    assert normalize_car_keyword("그랑 콜레오스") == "그랑콜레오스"


def test_normalize_removes_parentheses():
    assert normalize_car_keyword("콜레오스(QM6)") == "콜레오스"
    assert normalize_car_keyword("QM6 (콰트로)") == "QM6"


def test_normalize_plain_passthrough():
    assert normalize_car_keyword("셀토스") == "셀토스"
    assert normalize_car_keyword("토레스") == "토레스"
    assert normalize_car_keyword("액티언") == "액티언"


def test_normalize_blank_and_none():
    assert normalize_car_keyword("") == ""
    assert normalize_car_keyword("   ") == ""
    assert normalize_car_keyword(None) == ""


def test_normalize_strips_edges():
    assert normalize_car_keyword("  아반떼  ") == "아반떼"


# ── 키워드 생성 ──────────────────────────────────────────────────────────────

def test_build_keyword_default_product():
    # 공백 없는 단일 토큰 (키워드도구 hintKeywords 가 공백 구문을 거부 + 체험단 키워드 단위 규약)
    assert build_keyword("그랑 콜레오스") == "그랑콜레오스에어컨필터"


def test_build_keyword_custom_product():
    assert build_keyword("셀토스", "와이퍼") == "셀토스와이퍼"


def test_build_keyword_has_no_space():
    assert " " not in build_keyword("그랑 콜레오스")


def test_build_keyword_blank_returns_empty():
    assert build_keyword("") == ""
    assert build_keyword("()") == ""


# ── 차종 목록 추출(헤더 탐지 포함) ──────────────────────────────────────────

_SHEET_HEADER = ["브랜드", "차종", "상세차량명", "연식", "A-품번", "P-품번", "비고"]


def test_extract_models_basic():
    values = [
        _SHEET_HEADER,
        ["르노", "그랑 콜레오스", "그랑콜레오스 2024", "24-현재", "A-100", "P-200", ""],
        ["기아", "셀토스", "셀토스 SP2", "19-현재", "A-101", "P-201", ""],
    ]
    assert extract_models(values) == ["그랑 콜레오스", "셀토스"]


def test_extract_models_dedup_and_blank():
    values = [
        _SHEET_HEADER,
        ["기아", "셀토스", "a", "", "", "", ""],
        ["기아", "셀토스", "b", "", "", "", ""],   # 중복 차종 → 1회만
        ["기아", "", "c", "", "", "", ""],          # 빈 차종 → 스킵
        ["KGM", "토레스", "d", "", "", "", ""],
    ]
    assert extract_models(values) == ["셀토스", "토레스"]


def test_extract_models_limit():
    values = [_SHEET_HEADER] + [
        ["x", f"차종{i}", "", "", "", "", ""] for i in range(50)
    ]
    out = extract_models(values, limit=20)
    assert len(out) == 20
    assert out[0] == "차종0"
    assert out[-1] == "차종19"


def test_extract_models_header_not_first_row():
    # 상단에 안내문구가 있고 헤더가 2행일 때도 '차종' 컬럼을 찾아낸다.
    values = [
        ["에어컨필터 조견표", "", "", "", "", "", ""],
        _SHEET_HEADER,
        ["현대", "아반떼", "아반떼 CN7", "", "", "", ""],
    ]
    assert extract_models(values) == ["아반떼"]


def test_extract_models_empty():
    assert extract_models([]) == []


def test_default_car_col_is_차종():
    assert DEFAULT_CAR_COL == "차종"


# ── 검색량 조회 + 수확 (대역 어댑터) ─────────────────────────────────────────

def test_keyword_volume_matches_row():
    ad = _FakeAdapter({"그랑콜레오스에어컨필터": (1000, 290)})
    assert keyword_volume(ad, "그랑콜레오스에어컨필터") == 1290


def test_keyword_volume_no_match_returns_zero():
    ad = _FakeAdapter({})   # 응답에 매칭 행 없음
    assert keyword_volume(ad, "없는키워드에어컨필터") == 0


def test_harvest_basic_shape_and_sort():
    ad = _FakeAdapter({
        "그랑콜레오스에어컨필터": (1000, 290),   # 1290
        "셀토스에어컨필터": (500, 0),            # 500
    })
    items, failed = harvest_jogyeonpyo_kw_items(ad, ["그랑 콜레오스", "셀토스"], "에어컨필터")
    assert failed == []
    # 검색량 내림차순 + (keyword, car_model, volume) 형식
    assert items == [
        ("그랑콜레오스에어컨필터", "그랑 콜레오스", 1290),
        ("셀토스에어컨필터", "셀토스", 500),
    ]


def test_harvest_filters_zero_volume():
    ad = _FakeAdapter({"셀토스에어컨필터": (500, 0)})   # 토레스는 vol 0 → 제외
    items, failed = harvest_jogyeonpyo_kw_items(ad, ["셀토스", "토레스"], "에어컨필터")
    assert [c for _, c, _ in items] == ["셀토스"]
    assert failed == []   # vol 0 은 실패가 아님


def test_harvest_isolates_failures():
    ad = _FakeAdapter(
        {"셀토스에어컨필터": (500, 0)},
        fail_kws=("토레스에어컨필터",),   # 429 소진 가정
    )
    items, failed = harvest_jogyeonpyo_kw_items(ad, ["셀토스", "토레스"], "에어컨필터")
    assert [c for _, c, _ in items] == ["셀토스"]
    assert failed == ["토레스"]


def test_harvest_progress_callback_called_per_model():
    ad = _FakeAdapter({"셀토스에어컨필터": (500, 0)})
    seen = []
    harvest_jogyeonpyo_kw_items(
        ad, ["셀토스", "토레스"], "에어컨필터",
        on_progress=lambda done, total: seen.append((done, total)),
    )
    assert seen == [(1, 2), (2, 2)]


def test_harvest_rate_limit_sleep_between_calls():
    ad = _FakeAdapter({"셀토스에어컨필터": (500, 0), "토레스에어컨필터": (300, 0)})
    harvest_jogyeonpyo_kw_items(ad, ["셀토스", "토레스"], "에어컨필터")
    assert ad.sleeps == 1   # 첫 호출 전엔 sleep 안 함 → 2개 차종이면 1회


def test_harvest_skips_empty_keyword():
    ad = _FakeAdapter({"셀토스에어컨필터": (500, 0)})
    items, failed = harvest_jogyeonpyo_kw_items(ad, ["()", "셀토스"], "에어컨필터")
    assert [c for _, c, _ in items] == ["셀토스"]
    assert failed == []
