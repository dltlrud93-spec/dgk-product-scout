"""
naver_adapter.py — DataAdapter 의 라이브 네이버 검색광고 키워드도구 구현.

오빠두 CSV(CSVAdapter)는 한 번에 1개 키워드만 나와 '발굴'이 안 된다(검증 완료).
이 어댑터는 시드 키워드 리스트를 받아 키워드도구 API(GET /keywordstool)를
자동 호출 → 연관키워드(relKeyword)까지 수확 → 후보 카테고리(CategoryObservation)로 만든다.

공식 규격(naver/searchad-apidoc python-sample 으로 검증, 추측 아님):
  - GET https://api.searchad.naver.com/keywordstool
  - 파라미터: hintKeywords(쉼표구분, 호출당 최대 5개), showDetail=1
  - 서명: message="{timestamp}.{method}.{uri}" → HMAC-SHA256(secret) → base64
          (uri는 path만, 쿼리스트링 제외 — 이슈 #207 확인)
  - 헤더: X-Timestamp / X-API-KEY / X-Customer / X-Signature
  - 응답: {"keywordList":[{relKeyword, monthlyPcQcCnt, monthlyMobileQcCnt,
          plAvgDepth, compIdx, ...}]}, compIdx="낮음|중간|높음", 저소량="< 10" 문자열.

신호 매핑(스펙 3.2, CSVAdapter 와 동일 — 같은 로직 재사용):
  - 신호 7(시장 규모) = '기기군 합산' monthlyPcQcCnt + monthlyMobileQcCnt
  - 신호 8(광고 의존도) = compIdx(경쟁정도) + plAvgDepth(월평균노출광고수)
소모품 사전 + 기기 가드도 CSVAdapter 의 _is_consumable 을 그대로 쓴다.

연관키워드 → 후보 카테고리 매핑(기기군 합산 재설계):
  실데이터 근거(대표님 성공사례 벤치마크): 호환 소모품은 개별 키워드가 작게 흩어진다.
    · 와이퍼: 개별 중앙값 60인데 기기군 합산 179,730(최대 키워드 '워셔액' 20,850).
    · 에어컨필터: 중앙값 150인데 합산 346,520(최대 '자동차에어컨필터' 22,360).
  → 시장 규모는 '개별 키워드'가 아니라 '기기군 합산'으로 봐야 진짜가 보인다.

  중요: '워셔액'은 '와이퍼'를 부분문자열로 포함하지 않는다. 따라서 substring 귀속으론
  기기군 합산이 불가능하다. 키워드도구는 한 호출에 여러 hint 를 넣으면 응답에 어느
  hint 의 연관어인지 표시가 없으므로, 시드별 합산을 하려면 '시드당 1회 호출'이 필수다.
  → 멀티시드 배치는 폐기하고 시드마다 1회 호출한다(호출 간 rate limit sleep 유지).

  처리:
    1) 시드마다 hintKeywords=<시드 1개> 로 호출.
    2) 응답의 소모품 연관어(_is_consumable 통과) 전부를 그 시드의 '기기군 멤버'로.
       (substring 귀속 없음 — 응답 자체가 그 시드의 연관어이므로 '워셔액'도 포함됨)
    3) 기기군 카테고리 1건/시드: category_search_volume = Σ(멤버 PC+모바일) = 기기군 합산.
       구성한 개별 키워드는 member_keywords 로 보존(보조표시용).

⚠️ 소모품 필터는 부분 문자열 매칭이라 근사다(오매칭 가능). 결과는 사람 확인 필요.

보안: 키는 환경변수(.env)로만 받는다. 미설정 시 어느 키가 없는지 명시 예외.
      조용한 mock 폴백은 하지 않는다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import statistics
import time
from typing import Callable, Optional

import config
from src.adapters.base import DataAdapter

# CSVAdapter 와 동일한 소모품/파싱/집계 로직을 재사용한다(중복 정의·로직 분기 방지).
from src.adapters.csv_adapter import (
    _aggregate_comp_idx,
    _is_consumable,
    _parse_volume,
)
from src.schema import CategoryObservation

# --- 응답 필드명(공식 RelKwdStat 응답 키) ---------------------------------
_RESPONSE_KEYWORD_LIST = "keywordList"
_FIELD_REL_KEYWORD = "relKeyword"
_FIELD_MONTHLY_PC = "monthlyPcQcCnt"
_FIELD_MONTHLY_MOBILE = "monthlyMobileQcCnt"
_FIELD_AD_DEPTH = "plAvgDepth"
_FIELD_COMP_IDX = "compIdx"

# --- 환경변수 이름(키는 코드/깃에 박지 않는다) ---------------------------
_ENV_API_KEY = "NAVER_AD_API_KEY"
_ENV_SECRET_KEY = "NAVER_AD_SECRET_KEY"
_ENV_CUSTOMER_ID = "NAVER_AD_CUSTOMER_ID"

_HTTP_OK = 200
_HTTP_TOO_MANY_REQUESTS = 429
_METHOD = "GET"


class NaverAdConfigError(RuntimeError):
    """필수 환경변수(키)가 없을 때. 어느 키가 없는지 메시지에 담는다(조용한 폴백 금지)."""


class NaverAdAPIError(RuntimeError):
    """키워드도구 API 호출 실패(비200 또는 429 재시도 소진). 상태코드+본문을 담는다."""


def make_signature(timestamp: str, method: str, uri: str, secret_key: str) -> str:
    """
    공식 서명 생성: message="{timestamp}.{method}.{uri}" → HMAC-SHA256(secret) → base64.

    uri 는 path 만 넣는다(쿼리스트링 제외). 출처: naver/searchad-apidoc
    python-sample/examples/signaturehelper.py.
    """
    message = f"{timestamp}.{method}.{uri}"
    digest = hmac.new(
        secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode("ascii")


class NaverAdapter(DataAdapter):
    """시드 키워드 → 키워드도구 API 자동 호출 → 연관키워드 수확 → CategoryObservation."""

    def __init__(
        self,
        seed_keywords: list[str],
        *,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        customer_id: Optional[str] = None,
        rate_limit_seconds: Optional[float] = None,
        max_retries: Optional[int] = None,
        backoff_seconds: Optional[float] = None,
        http_get: Optional[Callable] = None,
        sleep: Optional[Callable[[float], None]] = None,
    ):
        """
        seed_keywords: 발굴 출발점이 되는 베이스 기기/카테고리 키워드들.
        키 인자(api_key 등)는 명시하지 않으면 환경변수에서 읽는다(테스트 편의 + .env).
        http_get / sleep 은 테스트에서 주입(실사용은 requests.get / time.sleep).
        """
        self.seed_keywords = [s.strip() for s in seed_keywords if s and s.strip()]
        self.api_key = api_key or os.environ.get(_ENV_API_KEY)
        self.secret_key = secret_key or os.environ.get(_ENV_SECRET_KEY)
        self.customer_id = customer_id or os.environ.get(_ENV_CUSTOMER_ID)
        self.rate_limit_seconds = (
            config.NAVER_AD_RATE_LIMIT_SECONDS
            if rate_limit_seconds is None
            else rate_limit_seconds
        )
        self.max_retries = (
            config.NAVER_AD_MAX_RETRIES if max_retries is None else max_retries
        )
        self.backoff_seconds = (
            config.NAVER_AD_BACKOFF_SECONDS if backoff_seconds is None else backoff_seconds
        )
        self._http_get = http_get
        self._sleep = sleep or time.sleep
        self._validate_keys()

    # ----- 키/보안 -----
    def _validate_keys(self) -> None:
        """필수 키가 하나라도 없으면 '어느 키가 없는지' 명시해 예외. 조용한 폴백 없음."""
        missing = [
            name
            for name, value in (
                (_ENV_API_KEY, self.api_key),
                (_ENV_SECRET_KEY, self.secret_key),
                (_ENV_CUSTOMER_ID, self.customer_id),
            )
            if not value
        ]
        if missing:
            raise NaverAdConfigError(
                "네이버 검색광고 API 키 미설정: "
                + ", ".join(missing)
                + " — .env 또는 환경변수에 설정하세요(.env.example 참조). "
                + "조용한 mock 폴백은 하지 않습니다."
            )

    # ----- HTTP 호출 -----
    def _do_get(self, url: str, params: dict, headers: dict):
        """주입된 http_get 이 있으면 그것을, 없으면 requests.get 을 지연 import 해 호출."""
        getter = self._http_get
        if getter is None:
            import requests  # 지연 import: 테스트는 http_get 주입으로 의존 없이 동작.

            getter = requests.get
        return getter(url, params=params, headers=headers)

    def _retry_wait(self, resp, attempt: int) -> float:
        """429 백오프 대기(초). Retry-After 헤더가 있으면 우선, 없으면 base*2**attempt."""
        resp_headers = getattr(resp, "headers", {}) or {}
        retry_after = resp_headers.get("Retry-After") or resp_headers.get("retry-after")
        if retry_after:
            try:
                return float(retry_after)
            except (TypeError, ValueError):
                pass
        return self.backoff_seconds * (2 ** attempt)

    def _request_keywordstool(self, hints: list[str]) -> list[dict]:
        """hintKeywords 1배치(≤5개)를 호출해 keywordList 를 반환. 429 자동 백오프 재시도."""
        uri = config.NAVER_AD_KEYWORDSTOOL_PATH
        url = config.NAVER_AD_BASE_URL + uri
        params = {"hintKeywords": ",".join(hints), "showDetail": "1"}

        attempt = 0
        while True:
            timestamp = str(round(time.time() * 1000))
            headers = {
                "X-Timestamp": timestamp,
                "X-API-KEY": self.api_key,
                "X-Customer": str(self.customer_id),
                "X-Signature": make_signature(timestamp, _METHOD, uri, self.secret_key),
            }
            resp = self._do_get(url, params, headers)
            status = getattr(resp, "status_code", None)

            if status == _HTTP_OK:
                body = resp.json() or {}
                return body.get(_RESPONSE_KEYWORD_LIST, []) or []

            # 429: 키 차단 방지 우선 → 백오프 후 재시도(소진 시 명확한 에러).
            if status == _HTTP_TOO_MANY_REQUESTS and attempt < self.max_retries:
                self._sleep(self._retry_wait(resp, attempt))
                attempt += 1
                continue

            raise NaverAdAPIError(
                f"키워드도구 호출 실패: status={status} "
                f"body={getattr(resp, 'text', '')!r} hints={hints}"
            )

    # ----- 수확 → 기기군 합산 카테고리 -----
    def _make_device_group(self, seed: str, items: list[dict]) -> CategoryObservation:
        """한 시드의 소모품 연관어(items)를 '기기군 합산'으로 집계해 CategoryObservation."""
        # 멤버별 개별 검색량(PC+모바일)을 먼저 구해 보조표시용으로도 보존한다.
        members: list[dict] = []
        for it in items:
            rel = str(it.get(_FIELD_REL_KEYWORD) or "").strip()
            vol = int(
                _parse_volume(it.get(_FIELD_MONTHLY_PC))
                + _parse_volume(it.get(_FIELD_MONTHLY_MOBILE))
            )
            members.append({"keyword": rel, "search_volume": vol})
        # 신호 7: '기기군 합산' 절대 월검색수(개별 키워드들을 모두 합산).
        search_volume = sum(m["search_volume"] for m in members)
        # 신호 8: 평균 노출광고수 + 대표 경쟁정도(멤버 집계).
        ad_depths = [_parse_volume(it.get(_FIELD_AD_DEPTH)) for it in items]
        avg_ad_depth = statistics.mean(ad_depths) if ad_depths else None
        comp_idx = _aggregate_comp_idx(
            [str(it.get(_FIELD_COMP_IDX) or "") for it in items]
        )
        # 검색량 큰 멤버부터 보이도록 정렬(보조표시 가독성).
        members.sort(key=lambda m: m["search_volume"], reverse=True)
        return CategoryObservation(
            category_name=f"{seed} 호환 소모품",
            discovery_pattern="호환소모품",
            # 키워드도구에 없는 신호(1·3·4·5)는 None → 보수적으로 0점 처리됨.
            base_device_bestseller_rank=None,
            base_device_search_volume=None,
            has_consumable=True,
            oem_price_krw=None,
            compatible_price_krw=None,
            repurchase_cycle_days=None,
            compatible_seller_count=None,
            # 신호 6: 스펙상 "거의 항상 충족" → True 가정(문서화된 가정).
            oem_producible=True,
            # 신호 7 입력 = 기기군 합산 검색량.
            category_search_volume=int(search_volume),
            # 신호 8 입력.
            comp_idx=comp_idx,
            avg_ad_depth=avg_ad_depth,
            # 보조표시: 합산을 구성한 개별 연관키워드 내역.
            member_keywords=members,
        )

    # ----- 인터페이스 구현 -----
    def fetch_category_observations(self) -> list[CategoryObservation]:
        # 시드당 1회 호출(멀티시드 배치 폐기 — per-seed 합산을 위해 필수).
        # 호출 간 rate limit sleep, 429 백오프는 _request_keywordstool 가 처리.
        observations: list[CategoryObservation] = []
        for i, seed in enumerate(self.seed_keywords):
            if i > 0:
                self._sleep(self.rate_limit_seconds)  # 호출 간 rate limit
            # 응답 = 이 시드의 연관어들. 같은 연관어 중복 출현은 1회만(부풀림 방지).
            seen: dict[str, dict] = {}
            for item in self._request_keywordstool([seed]):
                rel = str(item.get(_FIELD_REL_KEYWORD) or "").strip()
                if rel and rel not in seen:
                    seen[rel] = item
            # 소모품 사전+기기 가드 통과한 연관어 전부가 이 시드의 기기군 멤버.
            #   ('워셔액'처럼 시드를 문자열로 포함하지 않아도, 시드 응답이므로 포함됨)
            consumables = [it for rel, it in seen.items() if _is_consumable(rel)]
            if not consumables:
                continue  # 소모품 연관어가 없으면 기기군 후보 아님
            observations.append(self._make_device_group(seed, consumables))
        return observations
