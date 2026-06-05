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
# 미귀속 소모품 연관어 → 자체 후보 카테고리(발굴)
# ---------------------------------------------------------------------------
def test_unattributed_consumable_becomes_standalone_category():
    def getter(url, params=None, headers=None):
        return FakeResp(
            200,
            {
                "keywordList": [
                    _kw("공기청정기 필터", 10000, 10000, 1, "낮음"),  # 시드 귀속
                    _kw("헤파필터", 30000, 40000, 1, "낮음"),        # 미귀속 → 자체 후보
                    _kw("공기청정기 추천", 9000, 9000, 1, "낮음"),    # 비소모품 → 제외
                ]
            },
        )

    obs = _adapter(["공기청정기"], getter).fetch_category_observations()
    names = {o.category_name for o in obs}
    assert "공기청정기 호환 소모품" in names
    assert "헤파필터" in names
    hepa = next(o for o in obs if o.category_name == "헤파필터")
    assert hepa.discovery_pattern == "연관어발굴"
    assert hepa.category_search_volume == 70000  # 자체 검색량(30000+40000)
    assert hepa.has_consumable is True


def test_global_dedupe_across_batches():
    # 같은 연관어가 여러 배치 응답에 중복 출현해도 1회만 집계(부풀림 방지).
    def getter(url, params=None, headers=None):
        # 어느 배치든 동일 연관어 반환.
        return FakeResp(200, {"keywordList": [_kw("필터망", 1000, 1000, 1, "낮음")]})

    obs = _adapter(["로봇청소기", "공기청정기"], getter, batch_size=1).fetch_category_observations()
    standalone = [o for o in obs if o.category_name == "필터망"]
    assert len(standalone) == 1  # 두 배치에서 와도 하나로 dedupe.
    assert standalone[0].category_search_volume == 2000  # 중복 합산 안 됨.


# ---------------------------------------------------------------------------
# 배치 분할 + 호출 간 rate limit sleep
# ---------------------------------------------------------------------------
def test_batch_splitting_seven_seeds_two_calls():
    calls = []
    sleeps = []

    def getter(url, params=None, headers=None):
        calls.append(params["hintKeywords"].split(","))
        return FakeResp(200, {"keywordList": []})

    seeds = [f"키워드{i}" for i in range(7)]
    a = NaverAdapter(seeds, **KEYS, http_get=getter, sleep=lambda s: sleeps.append(s))
    a.fetch_category_observations()

    # 7개 시드, 배치 5 → 2회 호출(5 + 2).
    assert len(calls) == 2
    assert [len(c) for c in calls] == [5, 2]
    # 호출 간 rate limit sleep 1회(배치 2개 사이).
    assert sleeps == [config.NAVER_AD_RATE_LIMIT_SECONDS]


def test_rate_limit_default_is_generous():
    # 키 차단 방지: 기본 sleep 은 넉넉히(>= 0.3초).
    assert config.NAVER_AD_RATE_LIMIT_SECONDS >= 0.3


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
