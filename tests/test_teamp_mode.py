"""
test_teamp_mode.py — 체험단 타겟 선정 모드 unit test.

구조 변경(키워드 단위 재작업) 이후 기준:
  ① 비율 계산 정확     — 개별 키워드 검색량·문서수로 각각 계산(합산 없음)
  ② <10 제외           — volume=0 키워드는 harvest 단계에서 제외, 블로그 미호출
  ③ 3분류 경계         — classify_ratio 경계값 (gold=1.0, ok=3.0) 정확히
  ④ 황금 TOP 추출      — top_gold_kw_rows(검색량순) / top_gold_kw_rows_by_ratio(비율순)
  ⑤ 키워드 원문 그대로 — 차종 정규화·공백 삽입 없이 keyword 원문이 blog_fn 에 전달
  ⑥ 셀토스 검산        — 두 키워드 각각 별도 행, 각자 문서수로 비율 계산
  ⑦ 429 재시도 + 부분 실패

  [하위호환 유지]
  build_teamp_rows / top_gold_rows / _query_name 은 모듈에 그대로 존재 — 해당 테스트도 유지.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import config
from src.core.teamp_mode import (
    BlogFetchError,
    TeampKwRow,
    TeampRow,
    _query_name,
    build_teamp_rows,
    classify_ratio,
    fetch_blog_count,
    fetch_teamp_kw_rows_partial,
    fetch_teamp_rows_partial,
    harvest_teamp_kw_items,
    top_gold_kw_rows,
    top_gold_kw_rows_by_ratio,
    top_gold_rows,
    top_gold_rows_by_ratio,
)

# ---------------------------------------------------------------------------
# 키워드 단위 fixture (새 구조 — 합산 없음)
# ---------------------------------------------------------------------------
# 각 항목: (keyword, car_model_display, volume)
_KW_ITEMS_FIXTURE: list[tuple[str, str, int]] = [
    ("셀토스에어컨필터",    "셀토스KX3",        5000),
    ("아반떼CN7에어컨필터", "아반떼CN7",        1100),
    ("아반떼에어컨필터",    "아반떼(세대미상)", 1800),
    ("쏘렌토MQ4에어컨필터","쏘렌토MQ4",        1000),
    # volume=0 키워드는 harvest 단계에서 이미 제외돼 여기 없음
]

_KW_BLOG_COUNTS = {
    "셀토스에어컨필터":     2330,   # 비율 = 2330/5000 = 0.466  → 🟡 황금
    "아반떼CN7에어컨필터":  550,    # 비율 = 550/1100  = 0.500  → 🟡 황금
    "아반떼에어컨필터":     9000,   # 비율 = 9000/1800 = 5.000  → 🔴 포화
    "쏘렌토MQ4에어컨필터":  2000,   # 비율 = 2000/1000 = 2.000  → 🟢 해볼만
}


def _mock_kw_blog(query: str) -> int:
    return _KW_BLOG_COUNTS[query]


def _kw_rows_ok() -> list[TeampKwRow]:
    rows, failed = fetch_teamp_kw_rows_partial(_KW_ITEMS_FIXTURE, _mock_kw_blog, max_workers=1)
    assert failed == []
    return rows


# ---------------------------------------------------------------------------
# 하위호환 fixture (build_teamp_rows / top_gold_rows 계속 존재하므로 유지)
# ---------------------------------------------------------------------------
_AGG_FIXTURE = {
    ("아반떼CN7", "에어컨필터"):  {"volume": 1100, "members": 2, "ambiguous": False, "keywords": []},
    ("레이",      "에어컨필터"):  {"volume": 1800, "members": 3, "ambiguous": False, "keywords": []},
    ("쏘렌토MQ4", "에어컨필터"):  {"volume": 1000, "members": 2, "ambiguous": False, "keywords": []},
    ("투싼NX4",   "에어컨필터"):  {"volume":    0, "members": 1, "ambiguous": False, "keywords": []},
}

_BLOG_COUNTS_AGG = {
    "아반떼CN7 에어컨필터":  550,
    "레이 에어컨필터":       9000,
    "쏘렌토MQ4 에어컨필터":  2000,
}


def _mock_blog(query: str) -> int:
    return _BLOG_COUNTS_AGG[query]


def _rows() -> list[TeampRow]:
    return build_teamp_rows(_AGG_FIXTURE, "", "", blog_fetch_fn=_mock_blog)


# ---------------------------------------------------------------------------
# Mock adapter / index (harvest_teamp_kw_items 테스트용)
# ---------------------------------------------------------------------------
_HARVEST_KW_FIXTURE: dict[str, list[dict]] = {
    "에어컨필터": [
        {"relKeyword": "셀토스에어컨필터",    "monthlyPcQcCnt": 3000, "monthlyMobileQcCnt": 2000},
        {"relKeyword": "아반떼에어컨필터",    "monthlyPcQcCnt": 900,  "monthlyMobileQcCnt": 900},
        {"relKeyword": "제외에어컨필터없음",  "monthlyPcQcCnt": "< 10", "monthlyMobileQcCnt": "< 10"},
        {"relKeyword": "워셔액",              "monthlyPcQcCnt": 4000, "monthlyMobileQcCnt": 16000},  # 제품명 미포함
    ],
    "자동차에어컨필터": [
        {"relKeyword": "셀토스자동차에어컨필터", "monthlyPcQcCnt": 500, "monthlyMobileQcCnt": 300},
        {"relKeyword": "셀토스에어컨필터",        "monthlyPcQcCnt": 3000, "monthlyMobileQcCnt": 2000},  # 중복
    ],
}


class _MockAdapter:
    rate_limit_seconds = 0

    def _sleep(self, _: float) -> None:
        pass

    def _request_keywordstool(self, hints: list[str]) -> list[dict]:
        return _HARVEST_KW_FIXTURE.get(hints[0], [])


class _MockIndex:
    _MODELS = {"셀토스": "셀토스KX3", "아반떼": "아반떼(세대미상)"}

    def recognize(self, kw: str):
        for family, canonical in self._MODELS.items():
            if family in kw:
                return SimpleNamespace(recognized=True, canonical=canonical)
        return SimpleNamespace(recognized=False, canonical="")


# ─────────────────────────── ① 비율 계산 — 키워드 단위 ─────────────────────
def test_ratio_calculation_exact():
    """검색량·문서수·비율이 개별 키워드 기준으로 독립 계산된다(합산 없음)."""
    rows = {r.keyword: r for r in _kw_rows_ok()}
    assert rows["셀토스에어컨필터"].ratio    == pytest.approx(2330 / 5000)
    assert rows["아반떼에어컨필터"].ratio     == pytest.approx(9000 / 1800)
    assert rows["쏘렌토MQ4에어컨필터"].ratio  == pytest.approx(2000 / 1000)


def test_ratio_fields_are_independent_columns():
    """TeampKwRow: keyword·volume·doc_count·ratio 독립 컬럼 — 단일 합산 점수 없음."""
    import dataclasses
    fields = {f.name for f in dataclasses.fields(TeampKwRow)}
    assert "keyword" in fields
    assert "car_model" in fields
    assert "volume" in fields
    assert "doc_count" in fields
    assert "ratio" in fields
    assert "grade" in fields
    assert not (fields & {"score", "attractiveness", "매력도", "combined"})


# ─────────────────────────── ② <10 제외 — harvest 단계 ─────────────────────
def test_low_volume_excluded():
    """volume=0(원래 <10) 키워드는 harvest 단계에서 제외 — kw_items 에 없음."""
    result = harvest_teamp_kw_items(_MockAdapter(), ["에어컨필터"], _MockIndex())
    keywords = [kw for kw, _, _ in result]
    assert "셀토스에어컨필터" in keywords
    assert "제외에어컨필터없음" not in keywords   # volume=0 → 제외


def test_low_volume_zero_means_excluded_not_zero_ratio():
    """volume=0 키워드는 행 자체가 생략된다 — harvest 에서 제외, 0으로 나누기 아님."""
    class _ZeroAdapter:
        rate_limit_seconds = 0
        def _sleep(self, _): pass
        def _request_keywordstool(self, hints):
            return [{"relKeyword": "에어컨필터제로", "monthlyPcQcCnt": "< 10", "monthlyMobileQcCnt": "< 10"}]

    result = harvest_teamp_kw_items(_ZeroAdapter(), ["에어컨필터"], _MockIndex())
    assert result == []


# ─────────────────────────── ③ 3분류 경계 ──────────────────────────────────
@pytest.mark.parametrize("ratio,expected", [
    (0.0,    "🟡 황금"),
    (0.999,  "🟡 황금"),
    (1.0,    "🟢 해볼만"),
    (1.5,    "🟢 해볼만"),
    (3.0,    "🟢 해볼만"),
    (3.001,  "🔴 포화/후순위"),
    (10.0,   "🔴 포화/후순위"),
])
def test_classify_ratio_boundaries(ratio, expected):
    assert classify_ratio(ratio) == expected


def test_classify_uses_config_thresholds():
    assert config.TEAMP_RATIO_GOLD == 1.0
    assert config.TEAMP_RATIO_OK   == 3.0
    assert classify_ratio(0.5, gold=2.0, ok=5.0) == "🟡 황금"
    assert classify_ratio(2.5, gold=2.0, ok=5.0) == "🟢 해볼만"
    assert classify_ratio(6.0, gold=2.0, ok=5.0) == "🔴 포화/후순위"


def test_grade_field_matches_classify_kw():
    """TeampKwRow.grade 가 classify_ratio 결과와 일치한다."""
    rows = {r.keyword: r for r in _kw_rows_ok()}
    assert rows["셀토스에어컨필터"].grade    == "🟡 황금"
    assert rows["쏘렌토MQ4에어컨필터"].grade == "🟢 해볼만"
    assert rows["아반떼에어컨필터"].grade     == "🔴 포화/후순위"


# ─────────────────────────── ④ 황금 TOP 추출 ───────────────────────────────
def test_top_gold_kw_rows_by_volume():
    """top_gold_kw_rows: 황금 중 검색량 높은 순."""
    rows = _kw_rows_ok()
    top = top_gold_kw_rows(rows, n=10)
    # 황금: 셀토스(5000) > 아반떼CN7(1100)
    assert len(top) == 2
    assert top[0].keyword == "셀토스에어컨필터"
    assert top[0].grade == "🟡 황금"


def test_top_gold_kw_rows_by_ratio():
    """top_gold_kw_rows_by_ratio: 황금 중 비율 낮은 순."""
    rows = _kw_rows_ok()
    top = top_gold_kw_rows_by_ratio(rows, n=10)
    # 셀토스 비율 0.466 < 아반떼CN7 비율 0.500
    assert top[0].keyword == "셀토스에어컨필터"


def test_top_gold_kw_rows_n_limit():
    """n=1 이면 최대 1개만 반환."""
    rows = _kw_rows_ok()
    top1 = top_gold_kw_rows(rows, n=1)
    assert len(top1) == 1


def test_top_gold_kw_rows_excludes_non_gold():
    """top_gold_kw_rows 는 황금 이외(해볼만·포화)를 포함하지 않는다."""
    rows = _kw_rows_ok()
    for r in top_gold_kw_rows(rows, n=10):
        assert r.grade == "🟡 황금"


# ─────────────────────────── ⑤ 키워드 원문 그대로 ──────────────────────────
def test_kw_rows_sorted_by_ratio_ascending():
    """fetch_teamp_kw_rows_partial 기본 반환 = 비율 오름차순."""
    rows, _ = fetch_teamp_kw_rows_partial(_KW_ITEMS_FIXTURE, _mock_kw_blog, max_workers=1)
    ratios = [r.ratio for r in rows]
    assert ratios == sorted(ratios)


def test_keyword_blog_query_is_exact_keyword_string():
    """blog_fn 에 전달되는 쿼리 = keyword 원문 그대로 (차종 정규화·공백 삽입 없음)."""
    captured: list[str] = []

    def recording_blog(q: str) -> int:
        captured.append(q)
        return 100

    kw_items = [
        ("셀토스에어컨필터",        "셀토스KX3",        5000),
        ("아반떼에어컨필터",        "아반떼(세대미상)", 1800),
        ("셀토스자동차에어컨필터",  "셀토스KX3",         800),
    ]
    fetch_teamp_kw_rows_partial(kw_items, recording_blog, max_workers=1)

    assert "셀토스에어컨필터" in captured
    assert "아반떼에어컨필터" in captured
    assert "셀토스자동차에어컨필터" in captured
    # 괄호·세대미상·공백 삽입 없음
    assert all("(세대미상)" not in q for q in captured)
    assert all("셀토스KX3" not in q for q in captured)


def test_morning_aircon_old_bug_reproduced_and_fixed():
    """★ 회귀 방지: 키워드 원문 그대로 블로그 검색 — 차종 정규화 없음.

    구조 변경 전 버그: canonical "모닝(세대미상)" → _query_name("모닝") → "모닝 에어컨필터" 검색.
    더 이전 버그: "모닝(세대미상) 에어컨필터" → ~630 doc (황금 오분류).

    새 구조: 키워드 "모닝에어컨필터" → 블로그 검색 "모닝에어컨필터" (원문 그대로).
    정규화 없음 → "(세대미상)" 삽입·공백 삽입 구조적 불가 → 버그 원천 차단.
    """
    captured: list[str] = []

    def recording_blog(q: str) -> int:
        captured.append(q)
        return 18850

    kw_items = [("모닝에어컨필터", "모닝(세대미상)", 3260)]
    fetch_teamp_kw_rows_partial(kw_items, recording_blog, max_workers=1)

    assert captured == ["모닝에어컨필터"]
    assert "(세대미상)" not in captured[0]
    assert " " not in captured[0]   # 공백 삽입 없음


def test_morning_aircon_ratio_value():
    """모닝에어컨필터: docs=18850, vol=3260 → ratio ≈ 5.78 (포화)."""
    kw_items = [("모닝에어컨필터", "모닝(세대미상)", 3260)]
    rows, _ = fetch_teamp_kw_rows_partial(
        kw_items,
        blog_fn=lambda q: 18850,
        max_workers=1,
    )
    assert rows[0].ratio == pytest.approx(18850 / 3260, rel=1e-6)
    assert rows[0].grade == "🔴 포화/후순위"
    assert rows[0].keyword == "모닝에어컨필터"


# ─────────────────────────── ⑥ 셀토스 검산 ────────────────────────────────
def test_셀토스_two_keywords_separate_rows():
    """셀토스에어컨필터·셀토스자동차에어컨필터 → 각각 별도 행, 각자 문서수로 비율 계산.

    외부 도구 확인값: 셀토스에어컨필터 ~2,330 / 셀토스자동차에어컨필터 ~337.
    """
    kw_items = [
        ("셀토스에어컨필터",       "셀토스KX3", 5000),
        ("셀토스자동차에어컨필터", "셀토스KX3",  800),
    ]
    blog = {
        "셀토스에어컨필터":       2330,
        "셀토스자동차에어컨필터":  337,
    }
    rows, failed = fetch_teamp_kw_rows_partial(kw_items, lambda q: blog[q], max_workers=1)

    assert failed == []
    assert len(rows) == 2

    kw_map = {r.keyword: r for r in rows}
    assert "셀토스에어컨필터" in kw_map
    assert "셀토스자동차에어컨필터" in kw_map

    # 각자의 문서수로 비율 계산
    assert kw_map["셀토스에어컨필터"].doc_count       == 2330
    assert kw_map["셀토스자동차에어컨필터"].doc_count  == 337
    assert kw_map["셀토스에어컨필터"].ratio    == pytest.approx(2330 / 5000)
    assert kw_map["셀토스자동차에어컨필터"].ratio == pytest.approx(337 / 800)

    # 두 키워드 분류 확인 (자동차에어컨필터는 337/800=0.42 → 황금)
    assert kw_map["셀토스에어컨필터"].grade    == "🟡 황금"
    assert kw_map["셀토스자동차에어컨필터"].grade == "🟡 황금"


def test_harvest_teamp_kw_product_filter():
    """제품명 포함 키워드만 수확 결과에 포함 — 워셔액 등 무관 키워드 제외."""
    result = harvest_teamp_kw_items(_MockAdapter(), ["에어컨필터"], _MockIndex())
    keywords = [kw for kw, _, _ in result]

    assert any("에어컨필터" in kw for kw in keywords)
    assert "워셔액" not in keywords   # 제품명 미포함 → 제외


def test_harvest_teamp_kw_dedup_across_seeds():
    """두 시드에서 같은 키워드가 나와도 1건만 포함."""
    # "셀토스에어컨필터"가 "에어컨필터" · "자동차에어컨필터" 두 시드 모두에서 반환됨
    result = harvest_teamp_kw_items(
        _MockAdapter(), ["에어컨필터", "자동차에어컨필터"], _MockIndex()
    )
    keywords = [kw for kw, _, _ in result]
    assert keywords.count("셀토스에어컨필터") == 1   # 중복 없음


def test_harvest_teamp_kw_car_model_display_only():
    """harvest 결과의 car_model 은 index.recognize 표시용 — 필터 기준이 아님."""
    result = harvest_teamp_kw_items(_MockAdapter(), ["에어컨필터"], _MockIndex())
    kw_map = {kw: cm for kw, cm, _ in result}

    # 차종 인식된 키워드
    assert kw_map["셀토스에어컨필터"]  == "셀토스KX3"
    assert kw_map["아반떼에어컨필터"]  == "아반떼(세대미상)"


def test_volume_zero_never_queried():
    """volume=0 키워드는 harvest 단계에서 제외돼 blog_fn 에 전달되지 않는다."""
    calls: list[str] = []

    # harvest 결과에 volume=0 항목이 없으므로 fetch 단계에 도달 자체가 안 됨
    # 여기서는 harvest + fetch 연계를 mock adapter 로 검증
    result = harvest_teamp_kw_items(_MockAdapter(), ["에어컨필터"], _MockIndex())
    kw_items = result  # 이미 volume>0 필터 완료

    fetch_teamp_kw_rows_partial(kw_items, lambda q: (calls.append(q), 100)[1], max_workers=1)

    # "제외에어컨필터없음"(volume=0) 은 호출 안 됨
    assert all("제외" not in q for q in calls)


# ─────────────────────────── ⑦ 429 재시도 + 부분 실패 ──────────────────────

class _MockResponse:
    def __init__(self, status_code: int, total: int = 0, retry_after: str | None = None):
        self.status_code = status_code
        self._total = total
        self.headers = {"Retry-After": retry_after} if retry_after else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return {"total": self._total}


def test_fetch_blog_count_retries_on_429_then_succeeds():
    """429 두 번 → 200 성공: 올바른 total 반환."""
    responses = iter([
        _MockResponse(429),
        _MockResponse(429),
        _MockResponse(200, total=18850),
    ])
    sleeps: list[float] = []

    result = fetch_blog_count(
        "모닝에어컨필터", "cid", "csec",
        http_get=lambda *a, **kw: next(responses),
        sleep_fn=sleeps.append,
        max_retries=3,
        backoff_seconds=1.0,
        call_delay=0.0,
    )

    assert result == 18850
    # call_delay(0.0) + 첫 재시도(1.0) + 두 번째 재시도(2.0)
    assert sleeps == [0.0, 1.0, 2.0]


def test_fetch_blog_count_raises_after_max_retries():
    """429 응답이 max_retries+1 번 계속되면 BlogFetchError 발생."""
    always_429 = _MockResponse(429)

    with pytest.raises(BlogFetchError):
        fetch_blog_count(
            "레이에어컨필터", "cid", "csec",
            http_get=lambda *a, **kw: always_429,
            sleep_fn=lambda _: None,
            max_retries=3,
            backoff_seconds=1.0,
            call_delay=0.0,
        )


def test_fetch_blog_count_call_delay_applied():
    """call_delay 가 첫 호출 전에 sleep_fn 으로 전달된다."""
    sleeps: list[float] = []
    ok = _MockResponse(200, total=100)

    fetch_blog_count(
        "test", "cid", "csec",
        http_get=lambda *a, **kw: ok,
        sleep_fn=sleeps.append,
        max_retries=0,
        backoff_seconds=1.0,
        call_delay=0.15,
    )

    assert sleeps[0] == pytest.approx(0.15)


def test_fetch_blog_count_respects_retry_after_header():
    """Retry-After 헤더 값을 백오프 대신 사용한다."""
    responses = iter([
        _MockResponse(429, retry_after="5"),
        _MockResponse(200, total=42),
    ])
    sleeps: list[float] = []

    fetch_blog_count(
        "test", "cid", "csec",
        http_get=lambda *a, **kw: next(responses),
        sleep_fn=sleeps.append,
        max_retries=3,
        backoff_seconds=1.0,
        call_delay=0.0,
    )

    assert 5.0 in sleeps


def test_fetch_teamp_kw_rows_partial_all_succeed():
    """모든 항목 성공 → rows 는 비율 오름차순, failed 는 빈 리스트."""
    rows, failed = fetch_teamp_kw_rows_partial(_KW_ITEMS_FIXTURE, _mock_kw_blog, max_workers=3)

    assert failed == []
    assert len(rows) == 4
    ratios = [r.ratio for r in rows]
    assert ratios == sorted(ratios)


def test_fetch_teamp_kw_rows_partial_some_fail():
    """일부 항목 BlogFetchError → 해당 항목만 failed, 나머지 정상 반환."""
    def selective_blog(q: str) -> int:
        if "아반떼에어컨필터" == q:
            raise BlogFetchError("429 mock")
        return _KW_BLOG_COUNTS[q]

    rows, failed = fetch_teamp_kw_rows_partial(_KW_ITEMS_FIXTURE, selective_blog, max_workers=3)

    assert len(rows) == 3
    assert len(failed) == 1
    assert failed[0][0] == "아반떼에어컨필터"


def test_fetch_teamp_kw_rows_partial_all_fail():
    """모든 항목 실패 → rows 빈 리스트, failed 전체."""
    rows, failed = fetch_teamp_kw_rows_partial(
        _KW_ITEMS_FIXTURE,
        lambda q: (_ for _ in ()).throw(BlogFetchError("mock")),
        max_workers=2,
    )
    assert rows == []
    assert len(failed) == 4


def test_fetch_teamp_kw_rows_partial_progress_callback():
    """on_progress 콜백이 완료 항목마다 호출된다."""
    calls: list[tuple[int, int]] = []
    items = _KW_ITEMS_FIXTURE[:3]

    fetch_teamp_kw_rows_partial(
        items,
        blog_fn=lambda q: 100,
        max_workers=1,
        on_progress=lambda done, total: calls.append((done, total)),
    )

    assert len(calls) == 3
    assert calls[-1] == (3, 3)


def test_blog_fetch_error_is_runtime_error():
    """BlogFetchError 는 RuntimeError 하위 클래스."""
    assert issubclass(BlogFetchError, RuntimeError)


# ─────────────── [하위호환] build_teamp_rows / top_gold_rows 유지 ───────────
def test_compat_grade_field_matches_classify():
    """[하위호환] build_teamp_rows 의 grade 가 classify_ratio 와 일치한다."""
    rows = {r.canonical: r for r in _rows()}
    assert rows["아반떼CN7"].grade == "🟡 황금"
    assert rows["쏘렌토MQ4"].grade == "🟢 해볼만"
    assert rows["레이"].grade      == "🔴 포화/후순위"


def test_compat_top_gold_rows_by_volume():
    """[하위호환] top_gold_rows: 황금 중 검색량 높은 순."""
    top = top_gold_rows(_rows(), n=10)
    assert len(top) == 1
    assert top[0].canonical == "아반떼CN7"
    assert top[0].grade == "🟡 황금"


def test_compat_top_gold_rows_by_ratio():
    """[하위호환] top_gold_rows_by_ratio: 황금 중 비율 낮은 순."""
    top = top_gold_rows_by_ratio(_rows(), n=10)
    assert len(top) == 1
    assert top[0].canonical == "아반떼CN7"


def test_compat_top_gold_rows_n_limit():
    """[하위호환] n=1 이면 최대 1개."""
    agg_multi = {
        ("A차종", "제품"): {"volume": 2000, "members": 1, "ambiguous": False, "keywords": []},
        ("B차종", "제품"): {"volume": 3000, "members": 1, "ambiguous": False, "keywords": []},
        ("C차종", "제품"): {"volume": 1000, "members": 1, "ambiguous": False, "keywords": []},
    }
    blog = {"A차종 제품": 500, "B차종 제품": 600, "C차종 제품": 200}
    rows = build_teamp_rows(agg_multi, "", "", blog_fetch_fn=lambda q: blog[q])
    top1 = top_gold_rows(rows, n=1)
    assert len(top1) == 1
    assert top1[0].canonical == "B차종"


def test_compat_top_gold_excludes_non_gold():
    """[하위호환] top_gold_rows 는 황금 이외를 포함하지 않는다."""
    for r in top_gold_rows(_rows(), n=10):
        assert r.grade == "🟡 황금"


def test_compat_query_name_strips_sedate_misang():
    """[하위호환] _query_name: '(세대미상)' 접미사 제거."""
    assert _query_name("모닝(세대미상)") == "모닝"
    assert _query_name("아반떼(세대미상)") == "아반떼"


def test_compat_query_name_preserves_generation_code():
    """[하위호환] _query_name: 세대코드 있는 정규명은 그대로."""
    assert _query_name("아반떼CN7") == "아반떼CN7"
    assert _query_name("레이") == "레이"


def test_compat_build_rows_query_uses_clean_name():
    """[하위호환] build_teamp_rows 는 _query_name 으로 괄호 제거 후 검색한다."""
    captured: list[str] = []

    def recording_blog(q: str) -> int:
        captured.append(q)
        return 500

    agg = {
        ("모닝(세대미상)", "에어컨필터"): {"volume": 1000, "members": 1, "ambiguous": True, "keywords": []},
    }
    build_teamp_rows(agg, "", "", blog_fetch_fn=recording_blog)

    assert captured == ["모닝 에어컨필터"]   # _query_name 적용
    assert "(세대미상)" not in captured[0]


# [하위호환] fetch_teamp_rows_partial — 구 합산 단위 인터페이스 유지
def test_compat_fetch_teamp_rows_partial_all_succeed():
    valid = [("아반떼CN7", "에어컨필터", 1000), ("레이", "에어컨필터", 800)]
    blog = {"아반떼CN7 에어컨필터": 500, "레이 에어컨필터": 4800}
    from src.core.teamp_mode import _query_name as _qn
    rows, failed = fetch_teamp_rows_partial(valid, lambda q: blog[q], max_workers=1)
    assert failed == []
    assert len(rows) == 2


def test_compat_fetch_teamp_rows_partial_some_fail():
    valid = [("성공A", "에어컨필터", 1000), ("실패차종", "에어컨필터", 800), ("성공B", "에어컨필터", 600)]

    def selective(q: str) -> int:
        if "실패차종" in q:
            raise BlogFetchError("mock")
        return 500

    rows, failed = fetch_teamp_rows_partial(valid, selective, max_workers=3)
    assert len(rows) == 2
    assert len(failed) == 1
    assert failed[0][0] == "실패차종"
