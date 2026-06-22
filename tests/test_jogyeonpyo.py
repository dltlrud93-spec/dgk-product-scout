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
    keyword_volumes,
    normalize_car_keyword,
)


class _FakeAdapter:
    """검색광고 키워드도구 대역(★묶음 hint 지원) — vol_map[keyword]=(pc,mobile).

    hints 의 모든 키워드에 대해 vol>0 인 행을 반환(+관련어 노이즈 1줄). hints 중 하나라도
    fail_kws 에 있으면 예외(배치 단위 실패 재현). calls/sleeps 카운트.
    """

    def __init__(self, vol_map: dict, fail_kws: tuple = ()):
        self.rate_limit_seconds = 0.0
        self.vol_map = vol_map
        self.fail_kws = set(fail_kws)
        self.sleeps = 0
        self.calls = 0
        self.max_hints = 0

    def _sleep(self, _s):
        self.sleeps += 1

    def _request_keywordstool(self, hints):
        self.calls += 1
        self.max_hints = max(self.max_hints, len(hints))
        if any(h in self.fail_kws for h in hints):
            raise RuntimeError("429 Too Many Requests")
        rows = []
        for h in hints:
            pc, mob = self.vol_map.get(h, (0, 0))
            if pc or mob:
                rows.append(
                    {"relKeyword": h, "monthlyPcQcCnt": pc, "monthlyMobileQcCnt": mob})
        rows.append(   # 관련어 노이즈(매칭 안 되는 행) — 실제 응답 모사
            {"relKeyword": "관련어노이즈", "monthlyPcQcCnt": 9, "monthlyMobileQcCnt": 1})
        return rows


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


def test_harvest_isolates_failures_per_batch():
    """배치 단위 격리 — 실패 배치의 차종만 failed, 다른 배치는 정상."""
    ok = [f"차종{i}" for i in range(5)]    # 배치1(5개) 정상
    vol = {build_keyword(m, "에어컨필터"): (100, 0) for m in ok}
    ad = _FakeAdapter(vol, fail_kws=("토레스에어컨필터",))
    items, failed = harvest_jogyeonpyo_kw_items(
        ad, ok + ["토레스"], "에어컨필터")   # 배치2 = [토레스] → 실패
    assert sorted(c for _, c, _ in items) == sorted(ok)   # 배치1 정상
    assert failed == ["토레스"]                            # 배치2만 failed
    assert ad.calls == 2                                   # ⌈6/5⌉


def test_harvest_progress_callback_per_batch():
    """진행률은 배치 끝마다 누계로 갱신 + 빈 키워드는 호출 없이 선반영."""
    ad = _FakeAdapter({"셀토스에어컨필터": (500, 0)})
    seen = []
    harvest_jogyeonpyo_kw_items(
        ad, ["()", "셀토스"], "에어컨필터",          # "()" → 빈 키워드(선카운트)
        on_progress=lambda done, total: seen.append((done, total)),
    )
    assert seen[-1] == (2, 2)        # total 도달
    assert (1, 2) in seen            # 빈 키워드 선반영


def test_harvest_rate_limit_sleep_between_batches():
    """sleep 횟수 = 배치수 - 1(첫 배치 전엔 sleep 안 함)."""
    models = [f"차종{i}" for i in range(6)]   # 2배치
    vol = {build_keyword(m, "에어컨필터"): (100, 0) for m in models}
    ad = _FakeAdapter(vol)
    harvest_jogyeonpyo_kw_items(ad, models, "에어컨필터")
    assert ad.calls == 2
    assert ad.sleeps == 1            # 배치수(2) - 1


def test_harvest_skips_empty_keyword():
    ad = _FakeAdapter({"셀토스에어컨필터": (500, 0)})
    items, failed = harvest_jogyeonpyo_kw_items(ad, ["()", "셀토스"], "에어컨필터")
    assert [c for _, c, _ in items] == ["셀토스"]


# ── 묶음 검색량 조회(5배 가속) ───────────────────────────────────────────────
def test_keyword_volumes_batch_mapping():
    ad = _FakeAdapter({"A에어컨필터": (100, 0), "B에어컨필터": (0, 50),
                       "C에어컨필터": (10, 10)})
    vm = keyword_volumes(ad, ["A에어컨필터", "B에어컨필터", "C에어컨필터", "D에어컨필터"])
    assert vm == {"A에어컨필터": 100, "B에어컨필터": 50, "C에어컨필터": 20,
                  "D에어컨필터": 0}   # 매칭 없는 D 는 0
    assert ad.calls == 1             # 묶음 1회 호출


def test_harvest_batches_by_five():
    """12개 차종 → ⌈12/5⌉=3회만 호출(기존 12회 아님), hint 5개 초과 없음."""
    models = [f"차종{i}" for i in range(12)]
    vol = {build_keyword(m, "에어컨필터"): (100, 0) for m in models}
    ad = _FakeAdapter(vol)
    items, failed = harvest_jogyeonpyo_kw_items(ad, models, "에어컨필터")
    assert ad.calls == 3
    assert ad.max_hints <= 5         # API 5개 초과 hint 거부 — 절대 안 넘김
    assert len(items) == 12 and failed == []


def test_harvest_batch_size_capped_at_five():
    """batch_size>5 를 줘도 5로 캡(API 거부 방지)."""
    models = [f"차종{i}" for i in range(12)]
    vol = {build_keyword(m, "에어컨필터"): (100, 0) for m in models}
    ad = _FakeAdapter(vol)
    harvest_jogyeonpyo_kw_items(ad, models, "에어컨필터", batch_size=10)
    assert ad.calls == 3 and ad.max_hints <= 5


def test_harvest_sorted_and_zero_excluded_across_batches():
    """배치 넘나들며 검색량 내림차순 + 0 제외 유지."""
    models = [f"차종{i}" for i in range(7)]
    vol = {build_keyword(f"차종{i}", "에어컨필터"): (i * 100, 0) for i in range(7)}
    # 차종0 → vol 0 → 제외
    ad = _FakeAdapter(vol)
    items, failed = harvest_jogyeonpyo_kw_items(ad, models, "에어컨필터")
    vols = [v for _, _, v in items]
    assert vols == sorted(vols, reverse=True)
    assert all(v > 0 for v in vols)
    assert "차종0" not in [c for _, c, _ in items]   # vol 0 제외
    assert failed == []
