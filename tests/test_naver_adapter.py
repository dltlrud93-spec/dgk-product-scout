"""
test_naver_adapter.py — 라이브 NaverAdapter 검증(실제 키/네트워크 없이).

HTTP 호출부는 가짜 getter 주입(http_get)으로 모킹하고, sleep 도 주입해 실제 대기 없이:
  - HMAC 서명 생성 정확성(고정 timestamp → 독립 계산한 base64 와 일치)
  - 응답 파싱 → 신호 7/8 매핑
  - 연관키워드 수확 → 소모품 사전 필터 + 기기 가드
  - 미귀속 소모품 연관어 → 자체 후보 카테고리(발굴)
  - 배치 분할(시드 7개 → 호출 2회) + 호출 간 rate limit sleep
  - 키 미설정 예외(어느 키가 없는지 명시)
  - HTTP 429 백오프 재시도 / 비200 명확한 에러
를 검증한다.
"""

import pytest

import config
from src.adapters.naver_adapter import (
    NaverAdAPIError,
    NaverAdConfigError,
    NaverAdapter,
    make_signature,
)
from src.schema import CategoryObservation

# 테스트용 더미 키(실제 키 아님).
KEYS = dict(api_key="A", secret_key="test_secret_key", customer_id="123")


class FakeResp:
    """requests.Response 대용: status_code/json()/text/headers 만 노출."""

    def __init__(self, status_code=200, json_data=None, text="", headers=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._json


def _kw(rel, pc, mobile, depth, comp):
    """키워드도구 keywordList 항목 1개를 만든다(공식 응답 필드명)."""
    return {
        "relKeyword": rel,
        "monthlyPcQcCnt": pc,
        "monthlyMobileQcCnt": mobile,
        "plAvgDepth": depth,
        "compIdx": comp,
    }


def _adapter(seeds, getter, **over):
    """sleep 은 항상 no-op 으로 주입(실제 대기 없음)."""
    kw = dict(KEYS, http_get=getter, sleep=lambda *_: None)
    kw.update(over)
    return NaverAdapter(seeds, **kw)


# ---------------------------------------------------------------------------
# HMAC 서명 생성 정확성 (고정 timestamp)
# ---------------------------------------------------------------------------
def test_make_signature_fixed_timestamp():
    # 독립 계산값(message="1700000000000.GET./keywordstool", secret="test_secret_key").
    expected = "W6jp1m6VBSctKhWAq4QY7aqrjuQIn0dzwNyAJzPBLWk="
    sig = make_signature("1700000000000", "GET", "/keywordstool", "test_secret_key")
    assert sig == expected


def test_request_sends_all_four_signed_headers():
    captured = {}

    def getter(url, params=None, headers=None):
        captured.update(headers=headers, url=url, params=params)
        return FakeResp(200, {"keywordList": []})

    _adapter(["로봇청소기"], getter).fetch_category_observations()
    h = captured["headers"]
    # 헤더 4종 존재.
    assert set(["X-Timestamp", "X-API-KEY", "X-Customer", "X-Signature"]) <= set(h)
    # 서명은 그 호출의 timestamp + path 로 재계산한 값과 일치(쿼리스트링 제외).
    assert h["X-Signature"] == make_signature(
        h["X-Timestamp"], "GET", config.NAVER_AD_KEYWORDSTOOL_PATH, KEYS["secret_key"]
    )
    assert captured["url"].endswith("/keywordstool")
    assert captured["params"]["hintKeywords"] == "로봇청소기"
    assert captured["params"]["showDetail"] == "1"


# ---------------------------------------------------------------------------
# 키 미설정 예외 (조용한 폴백 금지)
# ---------------------------------------------------------------------------
def test_missing_all_keys_raises_naming_each(monkeypatch):
    for env in ("NAVER_AD_API_KEY", "NAVER_AD_SECRET_KEY", "NAVER_AD_CUSTOMER_ID"):
        monkeypatch.delenv(env, raising=False)
    with pytest.raises(NaverAdConfigError) as ei:
        NaverAdapter(["로봇청소기"])
    msg = str(ei.value)
    assert "NAVER_AD_API_KEY" in msg
    assert "NAVER_AD_SECRET_KEY" in msg
    assert "NAVER_AD_CUSTOMER_ID" in msg


def test_missing_one_key_names_only_that_one(monkeypatch):
    monkeypatch.setenv("NAVER_AD_API_KEY", "A")
    monkeypatch.setenv("NAVER_AD_CUSTOMER_ID", "123")
    monkeypatch.delenv("NAVER_AD_SECRET_KEY", raising=False)
    with pytest.raises(NaverAdConfigError) as ei:
        NaverAdapter(["로봇청소기"])
    assert "NAVER_AD_SECRET_KEY" in str(ei.value)
    assert "NAVER_AD_API_KEY" not in str(ei.value)


def test_keys_from_env_are_used(monkeypatch):
    monkeypatch.setenv("NAVER_AD_API_KEY", "A")
    monkeypatch.setenv("NAVER_AD_SECRET_KEY", "S")
    monkeypatch.setenv("NAVER_AD_CUSTOMER_ID", "123")
    a = NaverAdapter(["로봇청소기"], http_get=lambda *a, **k: FakeResp(200, {"keywordList": []}))
    assert a.secret_key == "S"


# ---------------------------------------------------------------------------
# 응답 파싱 → 신호 7/8 매핑 + 소모품 필터 + 시드 귀속
# ---------------------------------------------------------------------------
def test_response_parsing_signal7_8_and_consumable_filter():
    def getter(url, params=None, headers=None):
        return FakeResp(
            200,
            {
                "keywordList": [
                    _kw("로봇청소기 필터", 5000, 8000, 3, "중간"),
                    _kw("로봇청소기 브러쉬", 2000, 3000, 2, "중간"),
                    _kw("로봇청소기 추천", 9000, 9000, 8, "높음"),  # 비소모품 → 제외
                ]
            },
        )

    obs = _adapter(["로봇청소기"], getter).fetch_category_observations()
    assert len(obs) == 1
    o = obs[0]
    assert o.category_name == "로봇청소기 호환 소모품"
    assert o.discovery_pattern == "호환소모품"
    # 신호 7: 소모품 2행만 = (5000+8000)+(2000+3000) = 18000. 비소모품 제외.
    assert o.category_search_volume == 18000
    # 신호 8: 노출광고수 평균 (3+2)/2 = 2.5, 대표 경쟁정도 [중간,중간] → "중간".
    assert o.avg_ad_depth == pytest.approx(2.5)
    assert o.comp_idx == "중간"
    assert o.has_consumable is True


def test_low_volume_marker_not_inflating():
    def getter(url, params=None, headers=None):
        return FakeResp(200, {"keywordList": [_kw("가습기 필터", "< 10", "< 10", 1, "낮음")]})

    obs = _adapter(["가습기"], getter).fetch_category_observations()
    # "< 10" → LOW_VOLUME_FALLBACK(보수적) → 검색량 0.
    assert obs[0].category_search_volume == int(config.LOW_VOLUME_FALLBACK) * 2


def test_device_guard_excludes_device_keyword():
    # '로봇청소기교체'(STRONG 없이 기기어+약한 동작어) → 기기로 보고 제외.
    def getter(url, params=None, headers=None):
        return FakeResp(
            200,
            {"keywordList": [
                _kw("로봇청소기교체", 5000, 5000, 1, "낮음"),
                _kw("로봇청소기 필터", 5000, 5000, 1, "낮음"),
            ]},
        )

    obs = _adapter(["로봇청소기"], getter).fetch_category_observations()
    assert len(obs) == 1
    # 교체 행 제외되어 필터 1행만 → (5000+5000) = 10000.
    assert obs[0].category_search_volume == 10000


# ---------------------------------------------------------------------------
# 기기군 합산: cross-named 소모품도 시드 응답이면 합산 (재설계 핵심)
# ---------------------------------------------------------------------------
def test_cross_named_consumable_aggregated_into_device_group():
    # '헤파필터'는 '공기청정기'를 문자열로 포함하지 않지만, 공기청정기 응답이므로
    # 그 기기군에 합산돼야 한다(substring 귀속이 아니라 시드 응답 기준).
    def getter(url, params=None, headers=None):
        return FakeResp(
            200,
            {
                "keywordList": [
                    _kw("공기청정기 필터", 10000, 10000, 1, "낮음"),  # 시드 포함
                    _kw("헤파필터", 30000, 40000, 1, "낮음"),        # 시드 미포함 → 합산돼야
                    _kw("공기청정기 추천", 9000, 9000, 1, "낮음"),    # 비소모품 → 제외
                ]
            },
        )

    obs = _adapter(["공기청정기"], getter).fetch_category_observations()
    # 더는 '연관어발굴' 단독 카테고리를 만들지 않는다 — 시드당 기기군 1건.
    assert len(obs) == 1
    o = obs[0]
    assert o.category_name == "공기청정기 호환 소모품"
    assert o.discovery_pattern == "호환소모품"
    # 기기군 합산 = 공기청정기필터(20000) + 헤파필터(70000) = 90000. 비소모품 제외.
    assert o.category_search_volume == 90000
    # 보조표시(member_keywords)에 cross-named '헤파필터' 포함.
    kws = {m["keyword"] for m in o.member_keywords}
    assert "헤파필터" in kws and "공기청정기 필터" in kws
    assert sum(m["search_volume"] for m in o.member_keywords) == 90000


def test_dedupe_within_seed_response():
    # 한 시드 응답에 같은 연관어가 중복 출현해도 1회만 합산(부풀림 방지).
    def getter(url, params=None, headers=None):
        return FakeResp(
            200,
            {
                "keywordList": [
                    _kw("공기청정기 필터망", 1000, 1000, 1, "낮음"),
                    _kw("공기청정기 필터망", 1000, 1000, 1, "낮음"),  # 중복
                ]
            },
        )

    obs = _adapter(["공기청정기"], getter).fetch_category_observations()
    assert len(obs) == 1
    assert obs[0].category_search_volume == 2000  # 중복은 1회만
    assert len(obs[0].member_keywords) == 1


def test_same_keyword_under_two_seeds_counts_in_each_group():
    # 같은 소모품이 서로 다른 시드 응답에 오면 각 기기군의 멤버로 각각 합산(별개 카테고리).
    def getter(url, params=None, headers=None):
        return FakeResp(200, {"keywordList": [_kw("필터망", 1000, 1000, 1, "낮음")]})

    obs = _adapter(["로봇청소기", "공기청정기"], getter).fetch_category_observations()
    assert {o.category_name for o in obs} == {"로봇청소기 호환 소모품", "공기청정기 호환 소모품"}
    for o in obs:
        assert o.category_search_volume == 2000


# ---------------------------------------------------------------------------
# 시드당 1회 호출(멀티시드 배치 폐기) + 호출 간 rate limit sleep
# ---------------------------------------------------------------------------
def test_per_seed_one_call_each():
    calls = []
    sleeps = []

    def getter(url, params=None, headers=None):
        calls.append(params["hintKeywords"])
        return FakeResp(200, {"keywordList": []})

    seeds = [f"키워드{i}" for i in range(7)]
    a = NaverAdapter(seeds, **KEYS, http_get=getter, sleep=lambda s: sleeps.append(s))
    a.fetch_category_observations()

    # 멀티시드 배치 폐기 → 시드당 1회 호출(7개 → 7회), 호출당 hint 는 항상 1개.
    assert calls == seeds
    # 호출 간 rate limit sleep 6회(7호출 사이).
    assert sleeps == [config.NAVER_AD_RATE_LIMIT_SECONDS] * 6


def test_rate_limit_default_is_generous():
    # 키 차단 방지: 기본 sleep 은 넉넉히(>= 0.3초).
    assert config.NAVER_AD_RATE_LIMIT_SECONDS >= 0.3


# ---------------------------------------------------------------------------
# 기기군 합산 → 시장규모 4분면 분류 (경계 15만/3만 기준)
# ---------------------------------------------------------------------------
def test_large_aggregate_classified_as_dae():
    from src.ranking import discover

    def getter(url, params=None, headers=None):
        # 기기군 합산 = (80000+80000)+(20000+20000) = 200000 ≥ 150000 → '대'.
        return FakeResp(
            200,
            {
                "keywordList": [
                    _kw("에어컨 필터", 80000, 80000, 1, "낮음"),
                    _kw("에어컨필터 리필", 20000, 20000, 1, "낮음"),
                ]
            },
        )

    ranked = discover(_adapter(["에어컨"], getter))
    assert ranked[0].market_size_est == "대"


def test_small_aggregate_stays_so():
    from src.ranking import discover

    def getter(url, params=None, headers=None):
        # 기기군 합산 = (5000+5000)+(3000+3000) = 16000 < 30000 → '소'.
        return FakeResp(
            200,
            {
                "keywordList": [
                    _kw("미니 필터", 5000, 5000, 1, "낮음"),
                    _kw("미니필터 리필", 3000, 3000, 1, "낮음"),
                ]
            },
        )

    ranked = discover(_adapter(["미니가습기"], getter))
    assert ranked[0].market_size_est == "소"


# ---------------------------------------------------------------------------
# HTTP 429 백오프 재시도 / 비200 에러
# ---------------------------------------------------------------------------
def test_429_backoff_then_success():
    state = {"n": 0}
    sleeps = []

    def getter(url, params=None, headers=None):
        state["n"] += 1
        if state["n"] == 1:
            return FakeResp(429, text="rate limited")  # 첫 호출만 429
        return FakeResp(200, {"keywordList": [_kw("로봇청소기 필터", 100, 100, 1, "낮음")]})

    a = NaverAdapter(["로봇청소기"], **KEYS, http_get=getter, sleep=lambda s: sleeps.append(s))
    obs = a.fetch_category_observations()
    assert state["n"] == 2  # 429 후 재시도해 성공.
    assert len(sleeps) == 1  # 백오프 sleep 1회.
    assert obs[0].category_search_volume == 200


def test_429_honors_retry_after_header():
    state = {"n": 0}
    sleeps = []

    def getter(url, params=None, headers=None):
        state["n"] += 1
        if state["n"] == 1:
            return FakeResp(429, headers={"Retry-After": "7"})
        return FakeResp(200, {"keywordList": []})

    a = NaverAdapter(["로봇청소기"], **KEYS, http_get=getter, sleep=lambda s: sleeps.append(s))
    a.fetch_category_observations()
    assert sleeps == [7.0]  # Retry-After 우선.


def test_429_exhausted_raises():
    sleeps = []

    def getter(url, params=None, headers=None):
        return FakeResp(429, text="always limited")  # 계속 429

    a = NaverAdapter(
        ["로봇청소기"], **KEYS, http_get=getter, sleep=lambda s: sleeps.append(s), max_retries=2
    )
    with pytest.raises(NaverAdAPIError) as ei:
        a.fetch_category_observations()
    assert "429" in str(ei.value)
    assert len(sleeps) == 2  # max_retries 만큼만 재시도.


def test_non_200_raises_with_status():
    def getter(url, params=None, headers=None):
        return FakeResp(500, text="server error")

    with pytest.raises(NaverAdAPIError) as ei:
        _adapter(["로봇청소기"], getter).fetch_category_observations()
    assert "500" in str(ei.value)
    assert "server error" in str(ei.value)


# ---------------------------------------------------------------------------
# 인터페이스 계약
# ---------------------------------------------------------------------------
def test_returns_category_observations():
    def getter(url, params=None, headers=None):
        return FakeResp(200, {"keywordList": [_kw("로봇청소기 필터", 100, 100, 1, "낮음")]})

    obs = _adapter(["로봇청소기"], getter).fetch_category_observations()
    assert all(isinstance(o, CategoryObservation) for o in obs)
