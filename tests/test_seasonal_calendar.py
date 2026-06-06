"""
test_seasonal_calendar.py — 계절 캘린더 로직 결정론 검증 (Phase B-5).

라이브를 직접 단정할 수 없으므로(데이터랩/키워드도구 응답은 변함), 실제 응답을
tests/fixtures/*.json 으로 '스냅샷'해 고정하고, 계절성지수·단일규모·필터·정렬·분류·
추천후보 로직을 결정론적으로 검증한다(네트워크 없음 — 응답 주입).

fixture(스냅샷한 8개 대표 키워드, 6 케이스 커버):
  통과 2  : 차량용햇빛가리개, 차량용선풍기
  상시 4  : 워셔액, 와이퍼, 자동차에어컨필터, 엔진오일 (상시 대조군)
  경계/저 : 발수코팅제(약한 계절성), 성에제거기(진짜 계절·저규모)
"""

import json
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))  # seasonal_calendar 임포트용(conftest 는 루트만 추가)

import config
import seasonal_calendar as sc

_FIX = pathlib.Path(__file__).parent / "fixtures"
_DATALAB = json.loads((_FIX / "seasonal_datalab.json").read_text(encoding="utf-8"))
_KWTOOL = json.loads((_FIX / "seasonal_keywordstool.json").read_text(encoding="utf-8"))

SEASON_OF = {kw: v["season"] for kw, v in _DATALAB.items()}
KEYWORDS = list(_DATALAB.keys())


def _season_keywords() -> dict:
    d: dict = {}
    for kw, s in SEASON_OF.items():
        d.setdefault(s, []).append(kw)
    return d


# ----- fixture 주입용 가짜 응답 -----
def _fake_datalab(groups, cid, csec):
    """fetch_shape 가 주입받는 데이터랩 응답 — fixture 데이터를 results 형태로."""
    names = {g["groupName"] for g in groups}
    return {"results": [
        {"title": kw, "data": [{"period": p, "ratio": r} for p, r in _DATALAB[kw]["data"]]}
        for kw in names if kw in _DATALAB
    ]}


class _FakeResp:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._j = json_data
        self.text = ""
        self.headers = {}

    def json(self):
        return self._j


def _fake_adapter():
    """fetch_single_volumes 가 주입받는 어댑터 — fixture 정확일치 행 + 디코이를 반환."""
    from src.adapters.naver_adapter import NaverAdapter

    rows = [r for r in _KWTOOL.values() if r]
    # 디코이: exact-match 가 엉뚱한 고검색 행을 고르면 안 됨을 검증.
    rows = rows + [{"relKeyword": "전혀무관한키워드", "monthlyPcQcCnt": 999999,
                    "monthlyMobileQcCnt": 999999, "plAvgDepth": 1, "compIdx": "낮음"}]
    return NaverAdapter(
        KEYWORDS, api_key="A", secret_key="s", customer_id="1",
        http_get=lambda url, params=None, headers=None: _FakeResp(200, {"keywordList": rows}),
        sleep=lambda *a: None,
    )


def _shape():
    return sc.fetch_shape(_season_keywords(), "x", "y", request_datalab=_fake_datalab)


def _vols():
    return sc.fetch_single_volumes(KEYWORDS, adapter=_fake_adapter())


def _rows():
    shape, vols = _shape(), _vols()
    rows = []
    for kw in KEYWORDS:
        a = sc.analyze_shape(shape[kw])
        rows.append({
            "keyword": kw, "season": SEASON_OF[kw], "index": a["index"], "label": a["label"],
            "peak_month": a["peak_month"], "rising_month": a["rising_month"],
            "volume": vols[kw], "stability": a["stability"], "spread": a["spread"], "peaks": a["peaks"],
        })
    return rows


# ① 계절성 지수 계산 정확 (독립 재계산과 일치 = 검산)
def test_seasonality_index_matches_independent_calc():
    shape = _shape()
    for kw in ("차량용선풍기", "성에제거기", "워셔액"):
        rec = shape[kw]
        prof = [0.0] * 12
        cnt = [0] * 12
        for p, r in zip(rec["periods"], rec["ratios"]):
            m = int(p[5:7]) - 1
            prof[m] += r
            cnt[m] += 1
        prof = [prof[i] / cnt[i] if cnt[i] else 0.0 for i in range(12)]
        expected = max(prof) / (sum(prof) / 12)
        assert sc.analyze_shape(rec)["index"] == pytest.approx(expected)


# ② 단일 키워드 규모 정확 (PC+모바일, exact-match — 디코이 무시)
def test_single_keyword_volume_exact():
    vols = _vols()
    assert vols["차량용선풍기"]["volume"] == 23130   # 3530 + 19600
    assert vols["성에제거기"]["volume"] == 30        # 10 + 20
    assert vols["엔진오일"]["volume"] == 44250       # 8950 + 35300
    # 디코이(999999*2=1999998)를 고르지 않음 = exact-match 동작.
    assert all(v["volume"] != 1999998 for v in vols.values())


# ③ 필터 경계 (규모 20000 · 계절성 2.0)
def test_filter_boundaries():
    by = {r["keyword"]: r for r in _rows()}
    thr = config.MARKET_SIZE_THRESHOLD

    def passes(r):
        return sc._vol_num(r["volume"]) >= thr and r["index"] >= sc.FILTER_MIN_INDEX

    assert passes(by["차량용햇빛가리개"])      # 20,820 ≥ 2만 & 지수 ≥ 2.0 (양 축 경계 통과)
    assert not passes(by["워셔액"])            # 20,350 ≥ 2만 이나 1.68 < 2.0 → 계절성 미달
    assert not passes(by["와이퍼"])            # 19,430 < 2만 → 규모 미달
    assert not passes(by["자동차에어컨필터"])    # 21,960 ≥ 2만 이나 지수 < 2.0


# ④ 정렬 옵션 (규모순 / 계절성순)
def test_sort_options():
    rows = _rows()
    assert sc._sorted(rows, "scale")[0]["keyword"] == "엔진오일"        # 44,250 최대 규모
    assert sc._sorted(rows, "seasonality")[0]["keyword"] == "성에제거기"  # 5.46 최대 계절성


# ⑤ 상시 대조군이 상시로 정직 분류
def test_control_group_classified_as_always_on():
    shape = _shape()
    for kw in ("워셔액", "와이퍼", "자동차에어컨필터", "엔진오일"):
        assert sc.analyze_shape(shape[kw])["label"] == "상시상품"


# ⑥ 추천 후보 = 차량용선풍기 + 차량용햇빛가리개 (정확히 2개)
def test_recommended_candidates():
    thr = config.MARKET_SIZE_THRESHOLD
    cands = {
        r["keyword"] for r in _rows()
        if sc._vol_num(r["volume"]) >= thr and r["index"] >= sc.FILTER_MIN_INDEX
    }
    assert cands == {"차량용선풍기", "차량용햇빛가리개"}


# 임계/상수 확정 가드 (B-검수: 임계가 확정값으로 채워졌는가)
def test_confirmed_constants():
    assert config.MARKET_SIZE_THRESHOLD == 20_000
    assert sc.FILTER_MIN_INDEX == 2.0
    assert sc.SEASONALITY_STRONG == 2.0
    assert sc.STABILITY_TOLERANCE_MONTHS == 1
