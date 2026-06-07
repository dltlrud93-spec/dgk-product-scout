"""
test_car_demand.py — 차종 수요 스캐너 결정론 검증 (Phase C-5, Phase C 마감).

Phase B(test_seasonal_calendar)의 fixture 스냅샷 패턴 그대로: 라이브 데이터랩+키워드도구
응답을 tests/fixtures/car_*.json 으로 스냅샷해 고정하고(네트워크 없음 — 응답 주입),
C-1~C-4 동작(별칭 정규화 / 비모델 버림 / 모델 합산 / 규모 랭킹·컷 / 추세 / 규모·추세 별도
컬럼)을 회귀 테스트로 묶는다. 새 기능 없음 — 기존 동작 고정용.

6 케이스:
  ① 별칭 정규화      — 세대별 별도 정규명, bare 모호 버킷 분리, 제네시스 인식
  ② 비모델 토큰 버림 — 노이즈/단축별칭 가드 오매칭 방지
  ③ 모델별 합산 정확 — (정규명×부품유형) member_volume 합·교차시드 dedup
  ④ 규모 랭킹·컷     — 규모순 정렬 + MODEL_MIN_VOLUME 경계(1000 포함/999 제외)
  ⑤ 추세 계산        — 최근3÷과거12 비율 / endDate=직전 완전 월(부분월 회귀) / 신규후보 / 저신뢰
  ⑥ 규모·추세 별도   — 두 값이 단일 점수로 안 섞임, 출력에 매력도 점수 컬럼 없음
"""

import json
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))  # car_demand(scripts) 임포트용

import config
import car_demand as cd  # scripts/car_demand.py (규모+추세 랭킹 + 출력)
from src.core.car_demand import (
    ModelRow,
    Trend,
    compute_trend,
    harvest_models,
    model_member_keywords,
    rank_models,
)
from src.core.car_models import load_car_models
from src.core.search_volume import member_volume

_FIX = pathlib.Path(__file__).parent / "fixtures"
_KWTOOL = json.loads((_FIX / "car_keywordstool.json").read_text(encoding="utf-8"))
_DATALAB = json.loads((_FIX / "car_datalab.json").read_text(encoding="utf-8"))


def _idx():
    return load_car_models()  # 실제 data/car_models.json (인식 사전)


# ----- fixture 주입용 가짜 어댑터(시드별 keywordList 반환) -----
class _FakeResp:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._j = json_data
        self.text = ""
        self.headers = {}

    def json(self):
        return self._j


def _fake_adapter():
    """harvest_models 가 부르는 NaverAdapter — hintKeywords(시드)별 fixture 행을 반환."""
    from src.adapters.naver_adapter import NaverAdapter

    def http_get(url, params=None, headers=None):
        seed = (params or {}).get("hintKeywords", "")
        rows = [r for r in _KWTOOL.get(seed, []) if isinstance(r, dict)]
        return _FakeResp(200, {"keywordList": rows})

    return NaverAdapter(
        ["자동차에어컨필터"], api_key="A", secret_key="s", customer_id="1",
        http_get=http_get, sleep=lambda *a: None,
    )


def _agg():
    return harvest_models(_fake_adapter(), config.CAR_PART_SEEDS, _idx())


# ───────────────────────────── ① 별칭 정규화 ─────────────────────────────
def test_alias_normalization_generations_distinct():
    idx = _idx()
    md = idx.recognize("아반떼MD에어컨필터")
    ad = idx.recognize("아반떼AD에어컨필터")
    cn7 = idx.recognize("아반떼CN7에어컨필터")
    assert md.canonical == "아반떼MD"
    assert ad.canonical == "아반떼AD"
    assert cn7.canonical == "아반떼CN7"
    # 세대별로 각각 별도 정규명(같은 패밀리라도 다른 상품).
    assert len({md.canonical, ad.canonical, cn7.canonical}) == 3
    # 별칭 표기 차이는 같은 정규명으로 통일.
    assert idx.recognize("더뉴아반떼CN7캐빈필터").canonical == "아반떼CN7"


def test_bare_generation_goes_to_ambiguous_bucket():
    idx = _idx()
    bare = idx.recognize("아반떼에어컨필터")  # 세대 미상
    assert bare.ambiguous is True
    assert bare.canonical == "아반떼(세대미상)"
    # 모호 버킷은 구체 세대 어디에도 임의 귀속되지 않음.
    assert bare.canonical not in {"아반떼MD", "아반떼AD", "아반떼CN7"}


def test_genesis_recognized():
    assert _idx().recognize("제네시스G80캐빈필터").canonical == "제네시스G80"


# ───────────────────────────── ② 비모델 토큰 버림 ─────────────────────────────
@pytest.mark.parametrize("kw", [
    "워셔액", "불스원", "디스플레이오디오", "마스터실린더", "위닉스공기청정기",
])
def test_non_model_tokens_dropped(kw):
    assert _idx().recognize(kw).recognized is False


def test_short_alias_guard():
    idx = _idx()
    # 단축 별칭(마스터·레이)이 더 긴 비차종어의 부분문자열로 오매칭되지 않음.
    assert idx.recognize("마스터실린더").recognized is False     # '마스터' 가드
    assert idx.recognize("와이퍼블레이드").recognized is False   # '레이'(블레이드 内) 가드
    # 단, 토큰 경계로 정상 등장하면 정상 인식(가드가 정상 매칭까지 막지 않음).
    assert idx.recognize("마스터").canonical == "마스터"
    assert idx.recognize("레이와이퍼").canonical == "레이"


# ───────────────────────────── ③ 모델별 합산 정확 ─────────────────────────────
def test_model_volume_summation_exact():
    agg = _agg()
    # 아반떼CN7 / 에어컨필터 = 아반떼CN7에어컨필터(800) + 더뉴아반떼CN7캐빈필터(300).
    ac = agg[("아반떼CN7", "에어컨필터")]
    assert ac["volume"] == 1100
    assert ac["members"] == 2
    # 독립 재계산(검산) — fixture 행에서 member_volume 직접 합.
    rows = _KWTOOL["자동차에어컨필터"]
    expect = (member_volume(next(r for r in rows if r["relKeyword"] == "아반떼CN7에어컨필터"))
              + member_volume(next(r for r in rows if r["relKeyword"] == "더뉴아반떼CN7캐빈필터")))
    assert ac["volume"] == expect

    # 같은 모델이라도 부품유형이 다르면 별도 행(에어컨필터 ≠ 와이퍼).
    aw = agg[("아반떼CN7", "와이퍼")]
    assert aw["volume"] == 900  # 650 + 250
    assert aw["members"] == 2


def test_cross_seed_dedupe_no_inflation():
    # '아반떼CN7에어컨필터'가 캐빈필터 시드에 9999로 중복 등장해도 합산이 부풀지 않음(첫 시드 우선).
    assert _agg()[("아반떼CN7", "에어컨필터")]["volume"] == 1100


# ───────────────────────────── ④ 규모 랭킹·컷 ─────────────────────────────
def test_scale_ranking_sorted_desc():
    ranked = rank_models(_agg(), _idx(), config.MODEL_MIN_VOLUME)
    vols = [r.volume for r in ranked]
    assert vols == sorted(vols, reverse=True)
    assert vols == [1800, 1500, 1100, 1000]
    assert ranked[0].canonical == "레이"  # 최대 규모


def test_min_volume_cut_boundary():
    agg = _agg()
    ranked = {(r.canonical, r.part_type): r for r in rank_models(agg, _idx(), config.MODEL_MIN_VOLUME)}
    assert config.MODEL_MIN_VOLUME == 1000
    # 1000 은 포함(>=), 999 는 제외.
    assert ("쏘렌토MQ4", "에어컨필터") in ranked          # ==1000 포함
    assert ("투싼NX4", "와이퍼") not in ranked            # ==999 제외
    assert ("아반떼CN7", "와이퍼") not in ranked          # ==900 제외
    # 컷 없음(None)이면 999·900 행도 살아있음.
    nocut = {(r.canonical, r.part_type) for r in rank_models(agg, _idx(), None)}
    assert ("투싼NX4", "와이퍼") in nocut
    assert ("아반떼CN7", "와이퍼") in nocut


# ───────────────────────────── ⑤ 추세 계산 ─────────────────────────────
def _series(model):
    data = _DATALAB[model]["data"]
    return [p for p, _ in data], [float(r) for _, r in data]


def test_trend_ratio_exact():
    periods, ratios = _series("아반떼CN7")
    t = compute_trend(periods, ratios,
                      recent_months=config.TREND_RECENT_MONTHS,
                      baseline_months=config.TREND_BASELINE_MONTHS,
                      near_zero=config.TREND_BASELINE_NEAR_ZERO,
                      up=config.TREND_UP_THRESHOLD, down=config.TREND_DOWN_THRESHOLD)
    # 독립 재계산: 최근 3 평균 ÷ 직전 12 평균.
    expect = (sum(ratios[-3:]) / 3) / (sum(ratios[-15:-3]) / 12)
    assert t.ratio == pytest.approx(expect)
    assert t.ratio == pytest.approx(1.3)
    assert t.direction == "↑"
    assert t.new_candidate is False
    assert t.data_insufficient is False


def test_trend_new_candidate_flag():
    periods, ratios = _series("쏘렌토MQ4")  # baseline≈0, 최근 신호 → 신규 후보
    t = compute_trend(periods, ratios,
                      recent_months=config.TREND_RECENT_MONTHS,
                      baseline_months=config.TREND_BASELINE_MONTHS,
                      near_zero=config.TREND_BASELINE_NEAR_ZERO,
                      up=config.TREND_UP_THRESHOLD, down=config.TREND_DOWN_THRESHOLD)
    assert t.new_candidate is True
    assert t.direction == "신규 후보"
    assert t.ratio is None  # baseline≈0 → 비율 계산 금지


def test_trend_data_insufficient():
    periods, ratios = _series("투싼NX4")  # 3개월(<recent+1) → 데이터부족
    t = compute_trend(periods, ratios,
                      recent_months=config.TREND_RECENT_MONTHS,
                      baseline_months=config.TREND_BASELINE_MONTHS,
                      near_zero=config.TREND_BASELINE_NEAR_ZERO,
                      up=config.TREND_UP_THRESHOLD, down=config.TREND_DOWN_THRESHOLD)
    assert t.data_insufficient is True
    assert t.direction == "데이터부족"


def test_trend_enddate_is_last_complete_month():
    """★ 부분(진행중) 월 버그 회귀 방지 — fetch_trends 가 데이터랩에 보내는 endDate 는
    '직전 완전 월'의 말일이어야 한다(당월 부분 데이터를 recent 에 넣으면 전 모델이 거짓 ↓)."""
    from datetime import date, timedelta

    captured = {}

    def fake_request(groups, start, end, cid, csec):
        captured["start"], captured["end"] = start, end
        return {"results": []}

    # 모듈 전역 _request_datalab 를 가짜로 교체(네트워크 없음).
    orig = cd._request_datalab
    cd._request_datalab = fake_request
    try:
        cd.fetch_trends({"아반떼CN7": [("아반떼CN7와이퍼", 650)]}, "cid", "csec")
    finally:
        cd._request_datalab = orig

    today = date.today()
    expected_end = date(today.year, today.month, 1) - timedelta(days=1)  # 직전 월 말일
    assert captured["end"] == expected_end.isoformat()
    # 당월(부분 월)은 절대 포함되지 않음.
    assert captured["end"] < date(today.year, today.month, 1).isoformat()
    # 시작일 = endDate 에서 (baseline+recent+여유3)개월 전 1일.
    span = config.TREND_BASELINE_MONTHS + config.TREND_RECENT_MONTHS + 3
    assert captured["start"] == cd._months_ago(expected_end, span).isoformat()


def test_low_confidence_flags():
    """저신뢰 플래그: 멤버1 또는 규모 하한(TREND_LOWCONF_VOLUME 미만) 또는 시계열부족."""
    healthy = Trend(ratio=1.3, direction="↑", new_candidate=False,
                    recent_avg=13.0, baseline_avg=10.0, data_insufficient=False)

    def row(members, volume):
        return ModelRow("X", "에어컨필터", volume, members, False, "현대")

    assert cd._low_conf(row(members=1, volume=5000), healthy) is True   # 멤버1
    assert cd._low_conf(row(members=3, volume=1500), healthy) is True   # 규모 하한 근처
    assert cd._low_conf(row(members=3, volume=5000), healthy) is False  # 정상
    insufficient = Trend(None, "데이터부족", False, 0.0, 0.0, True)
    assert cd._low_conf(row(members=3, volume=5000), insufficient) is True


# ───────────────────────────── ⑥ 규모·추세 별도 컬럼 ─────────────────────────────
def test_scale_and_trend_not_merged_into_single_score():
    import dataclasses
    # ModelRow 에 단일 점수/매력도 필드가 없음(규모는 volume 한 칸).
    fields = {f.name for f in dataclasses.fields(ModelRow)}
    assert fields == {"canonical", "part_type", "volume", "members", "ambiguous", "maker"}
    assert not (fields & {"score", "attractiveness", "매력도", "combined", "total_score"})
    # 규모(ModelRow)와 추세(Trend)는 서로 다른 객체 — 한쪽이 다른쪽 값을 품지 않음.
    assert not hasattr(ModelRow("a", "b", 1, 1, False, ""), "ratio")
    assert not hasattr(Trend(1.0, "↑", False, 1.0, 1.0, False), "volume")


def test_sort_axes_are_independent():
    # 규모 순서 ≠ 추세 순서가 되도록 구성 → 정렬축이 분리돼 있음(단일 점수면 둘이 같아짐).
    a = ModelRow("A", "p", 2000, 2, False, "")  # 규모 큼, 추세 약함
    b = ModelRow("B", "p", 1000, 2, False, "")  # 규모 작음, 추세 강함
    trends = {
        "A": Trend(1.0, "보합", False, 10.0, 10.0, False),
        "B": Trend(5.0, "↑", False, 50.0, 10.0, False),
    }
    by_scale = [r.canonical for r in cd._sort_rows([a, b], trends, "scale")]
    by_trend = [r.canonical for r in cd._sort_rows([a, b], trends, "trend")]
    assert by_scale == ["A", "B"]  # 규모순
    assert by_trend == ["B", "A"]  # 추세순 — 규모순과 반대 = 두 축 독립


def test_output_has_no_single_score_column(capsys):
    rows = rank_models(_agg(), _idx(), config.MODEL_MIN_VOLUME)
    trends = {"레이": Trend(None, "신규 후보", True, 50.0, 0.0, False)}
    cd.render_table(rows, trends, "본표 (정렬: 규모순)")
    out = capsys.readouterr().out
    # 규모와 추세는 별도 컬럼 헤더로 존재.
    assert "합산검색량" in out
    assert "추세" in out
    # 단일 매력도/종합 점수 컬럼은 없음.
    assert "매력도" not in out
    assert "점수" not in out
