"""
test_scanner.py — 데이터 전수 스캔 엔진(plan_scan · scan_models) 순수 검증(네트워크 0).
"""

from __future__ import annotations

import datetime
from types import SimpleNamespace

from src.core.jogyeonpyo import build_keyword
from src.core.scanner import (
    make_batch_query,
    no_keyword_models,
    plan_scan,
    scan_models,
)


def _vault_row(model, product, scanned_at, status="정상", volume=100):
    return {
        "keyword": build_keyword(model, product),
        "product": product,
        "car_model": model,
        "scanned_at": scanned_at,
        "status": status,
        "volume": volume,
    }


# ── plan_scan ─────────────────────────────────────────────────────────────────

def test_plan_scan_skips_recent_within_window():
    """30일 이내 스캔된 키워드는 제외된다."""
    today = datetime.date(2026, 2, 1)
    models = ["셀토스", "모닝"]
    vault_rows = [
        _vault_row("셀토스", "에어컨필터", "2026-01-25 09:00"),  # 7일 전 → 스킵
    ]
    todo, skipped = plan_scan("에어컨필터", models, vault_rows, skip_days=30, today=today)
    assert todo == ["모닝"]     # 셀토스는 최근 스캔 → 제외
    assert skipped == 1


def test_plan_scan_rescans_when_older_than_window():
    """30일보다 오래된 스캔은 다시 포함."""
    today = datetime.date(2026, 2, 1)
    models = ["셀토스"]
    vault_rows = [_vault_row("셀토스", "에어컨필터", "2025-12-01 09:00")]  # 62일 전
    todo, skipped = plan_scan("에어컨필터", models, vault_rows, skip_days=30, today=today)
    assert todo == ["셀토스"]
    assert skipped == 0


def test_plan_scan_force_includes_all():
    today = datetime.date(2026, 2, 1)
    models = ["셀토스", "모닝"]
    vault_rows = [_vault_row("셀토스", "에어컨필터", "2026-01-31 09:00")]  # 아주 최근
    todo, skipped = plan_scan("에어컨필터", models, vault_rows, force=True, today=today)
    assert todo == ["셀토스", "모닝"]   # force → 전체
    assert skipped == 0


def test_plan_scan_missing_keyword_reincluded():
    """★시트에 행이 없는 키워드(과거 조회 실패 포함)는 자동으로 todo 재포함."""
    today = datetime.date(2026, 2, 1)
    models = ["셀토스", "모닝", "레이"]
    # 셀토스만 최근 스캔됨. 모닝·레이는 시트에 행 없음(미스캔 또는 과거 실패).
    vault_rows = [_vault_row("셀토스", "에어컨필터", "2026-01-30 09:00")]
    todo, skipped = plan_scan("에어컨필터", models, vault_rows, skip_days=30, today=today)
    assert todo == ["모닝", "레이"]   # 행 없는 키워드 재포함
    assert skipped == 1


def test_plan_scan_dormant_recent_also_skipped():
    """잠복(검색량<10)으로 최근 스캔된 키워드도 30일 이내면 스킵(재스캔 유지비 절약)."""
    today = datetime.date(2026, 2, 1)
    models = ["신차"]
    vault_rows = [_vault_row("신차", "에어컨필터", "2026-01-28 09:00", status="잠복", volume=3)]
    todo, skipped = plan_scan("에어컨필터", models, vault_rows, skip_days=30, today=today)
    assert todo == []
    assert skipped == 1


# ── scan_models ───────────────────────────────────────────────────────────────

class _BlogRecorder:
    """blog_fn 대역 — 호출된 키워드를 기록. total_map 없으면 기본값 반환."""

    def __init__(self, total_map=None, fail_kws=(), default=200):
        self.total_map = total_map or {}
        self.fail_kws = set(fail_kws)
        self.calls = []
        self.default = default

    def __call__(self, kw):
        self.calls.append(kw)
        if kw in self.fail_kws:
            raise RuntimeError("blog 429 소진")
        return self.total_map.get(kw, self.default)


def test_scan_models_dormant_skips_blog_query():
    """검색량<10 잠복 차종은 블로그 조회를 하지 않고 status='잠복' 지표 공란."""
    models = ["신차"]
    kw = build_keyword("신차", "에어컨필터")
    blog = _BlogRecorder()
    chunks = []
    scan_models(
        models, "에어컨필터",
        volumes_fn=lambda kws: {k: 5 for k in kws},   # 검색량 5 → 잠복
        blog_fn=blog,
        recent_fn=lambda k: 1,
        on_chunk=chunks.append,
        scanned_at="2026-02-01 09:00",
    )
    assert blog.calls == []                       # ★블로그 미호출
    rows = [r for c in chunks for r in c]
    assert len(rows) == 1
    row = rows[0]
    assert row[3] == kw and row[4] == 5           # keyword, volume
    assert row[-1] == "잠복"                        # status
    assert row[5] == "" and row[6] == "" and row[9] == ""   # doc_count/ratio/opp 공란


def test_scan_models_normal_fills_all_fields():
    models = ["셀토스"]
    kw = build_keyword("셀토스", "에어컨필터")
    scan_models_out = []
    scan_models(
        models, "에어컨필터",
        volumes_fn=lambda kws: {k: 1000 for k in kws},
        blog_fn=lambda k: 500,
        recent_fn=lambda k: 7,
        on_chunk=scan_models_out.append,
        scanned_at="2026-02-01 09:00",
    )
    row = scan_models_out[0][0]
    assert row[4] == 1000            # volume
    assert row[5] == 500             # doc_count
    assert row[6] == 0.5             # ratio = 500/1000
    assert row[8] == 7               # recent_3m
    assert row[9] == 667             # opportunity_score = round(1000*1000/(1000+500))
    assert row[-1] == "정상"


def test_scan_models_blog_failure_produces_no_row():
    """블로그 조회 실패 키워드는 행을 만들지 않는다(다음 실행 재시도)."""
    models = ["셀토스", "모닝"]
    kw_sel = build_keyword("셀토스", "에어컨필터")
    blog = _BlogRecorder(fail_kws=(kw_sel,))
    chunks = []
    scan_models(
        models, "에어컨필터",
        volumes_fn=lambda kws: {k: 1000 for k in kws},
        blog_fn=blog,
        recent_fn=lambda k: 7,
        on_chunk=chunks.append,
        scanned_at="2026-02-01 09:00",
    )
    rows = [r for c in chunks for r in c]
    kws = [r[3] for r in rows]
    assert kw_sel not in kws                      # 실패 → 행 없음
    assert build_keyword("모닝", "에어컨필터") in kws   # 성공 → 행 있음


def test_scan_models_volume_batch_failure_produces_no_row():
    """검색량 배치 예외 시 그 배치 키워드 전부 행 미생성."""
    models = ["셀토스", "모닝"]

    def _boom(_kws):
        raise RuntimeError("429 검색량 소진")

    chunks = []
    scan_models(
        models, "에어컨필터",
        volumes_fn=_boom,
        blog_fn=lambda k: 500,
        recent_fn=lambda k: 7,
        on_chunk=chunks.append,
        scanned_at="2026-02-01 09:00",
    )
    rows = [r for c in chunks for r in c]
    assert rows == []                              # 전부 실패 → 행 0


def test_scan_models_chunk_callback_count():
    """청크(=chunk_size)마다 on_chunk 1회 — 25개·크기10 → 3회."""
    models = [f"차종{i}" for i in range(25)]
    call_count = {"n": 0}

    def _cb(_rows):
        call_count["n"] += 1

    scan_models(
        models, "에어컨필터",
        volumes_fn=lambda kws: {k: 1000 for k in kws},
        blog_fn=lambda k: 500,
        recent_fn=lambda k: 7,
        on_chunk=_cb,
        chunk_size=10,
        scanned_at="2026-02-01 09:00",
    )
    assert call_count["n"] == 3                     # ⌈25/10⌉


def test_scan_models_recent_failure_keeps_row_blank_recent():
    """최신성 조회 실패는 치명 아님 — 행은 만들되 recent_3m 공란."""
    models = ["셀토스"]

    def _recent_boom(_kw):
        raise RuntimeError("recent 429")

    chunks = []
    scan_models(
        models, "에어컨필터",
        volumes_fn=lambda kws: {k: 1000 for k in kws},
        blog_fn=lambda k: 500,
        recent_fn=_recent_boom,
        on_chunk=chunks.append,
        scanned_at="2026-02-01 09:00",
    )
    row = chunks[0][0]
    assert row[-1] == "정상"
    assert row[8] == ""            # recent_3m 공란


# ── v1.1 §1: 배치 오염 격리 — 개별 재시도 폴백 ───────────────────────────────

class _PoisonVolumes:
    """배치에 poison 이 있으면(2개 이상 함께) 예외. 개별(1개) 호출은 poison 만 예외.

    solo(=1개) 호출을 기록해 개별 재시도가 실제로 일어났는지 검증한다.
    """

    def __init__(self, poison, vol=1000):
        self.poison = poison
        self.vol = vol
        self.solo_calls = []
        self.batch_calls = []

    def __call__(self, kws):
        if len(kws) == 1:
            self.solo_calls.append(kws[0])
            if kws[0] == self.poison:
                raise RuntimeError("400 BAD_REQUEST (독)")
            return {kws[0]: self.vol}
        self.batch_calls.append(list(kws))
        if self.poison in kws:
            raise RuntimeError("400 배치 오염")
        return {k: self.vol for k in kws}


def test_scan_models_batch_poison_individual_retry():
    """배치 예외 → 1개씩 개별 재시도. 독 1개만 실패, 나머지 4개는 정상 행."""
    models = ["A", "B", "C", "D", "E"]   # build_keyword → A에어컨필터 등 (5개 = 1배치)
    poison_kw = build_keyword("C", "에어컨필터")
    vol = _PoisonVolumes(poison_kw)
    chunks = []
    failures = scan_models(
        models, "에어컨필터",
        volumes_fn=vol,
        blog_fn=lambda k: 100,
        recent_fn=lambda k: 3,
        on_chunk=chunks.append,
        scanned_at="2026-02-01 09:00",
    )
    rows = [r for c in chunks for r in c]
    kws = {r[3] for r in rows}
    assert len(rows) == 4                       # 4/5 보존
    assert poison_kw not in kws                  # 독만 빠짐
    assert len(vol.solo_calls) == 5              # ★개별 재시도가 실제 5회 solo 호출
    assert len(failures) == 1
    assert failures[0]["keyword"] == poison_kw
    assert failures[0]["stage"] == "검색량"
    assert "RuntimeError" in failures[0]["reason"]


def test_scan_models_blog_failure_recorded_with_reason():
    """블로그 실패 키워드는 stage='블로그' + reason 으로 실패 목록에 남는다(행 미생성)."""
    models = ["셀토스"]
    kw = build_keyword("셀토스", "에어컨필터")

    def _blog_boom(_k):
        raise RuntimeError("블로그 429 소진")

    chunks = []
    failures = scan_models(
        models, "에어컨필터",
        volumes_fn=lambda kws: {k: 1000 for k in kws},
        blog_fn=_blog_boom,
        on_chunk=chunks.append,
        scanned_at="2026-02-01 09:00",
    )
    assert [r for c in chunks for r in c] == []   # 행 미생성
    assert len(failures) == 1
    assert failures[0]["keyword"] == kw
    assert failures[0]["stage"] == "블로그"
    assert failures[0]["model"] == "셀토스"
    assert "RuntimeError" in failures[0]["reason"]


def test_scan_models_no_failures_returns_empty_list():
    failures = scan_models(
        ["셀토스"], "에어컨필터",
        volumes_fn=lambda kws: {k: 1000 for k in kws},
        blog_fn=lambda k: 100,
        on_chunk=lambda rows: None,
        scanned_at="2026-02-01 09:00",
    )
    assert failures == []


# ── v1.1 §3: 빈 키워드 가드 ──────────────────────────────────────────────────

def test_plan_scan_excludes_empty_keyword():
    """build_keyword 가 빈 문자열인 차종은 todo 에서 제외된다."""
    models = ["셀토스", "(미상)"]   # '(미상)' → 괄호 제거 후 빈 문자열
    assert build_keyword("(미상)", "에어컨필터") == ""   # 전제 확인
    todo, _skipped = plan_scan("에어컨필터", models, [], today=datetime.date(2026, 2, 1))
    assert todo == ["셀토스"]


def test_no_keyword_models_lists_empty():
    models = ["셀토스", "(미상)", "   "]
    assert no_keyword_models("에어컨필터", models) == ["(미상)", "   "]


# ── v1.1 §4: 연관어 부수확 ───────────────────────────────────────────────────

class _FakeIndex:
    """recognize 대역 — recognized_map[kw] = canonical(없으면 미인식)."""

    def __init__(self, recognized_map):
        self.m = recognized_map

    def recognize(self, kw):
        canon = self.m.get(kw)
        return SimpleNamespace(recognized=canon is not None, canonical=canon)


def test_variant_harvest_filters_all_conditions():
    """변형 채택 필터 ①~⑤ — 통과분만 정상 행으로 저장."""
    product = "에어컨필터"
    req_kw = build_keyword("셀토스", product)   # 셀토스에어컨필터 (요청 키워드)
    adopt_kw = "셀토스자동차에어컨필터"

    variants = {
        adopt_kw: 500,                 # 채택 (①②③④⑤ 통과)
        req_kw: 1000,                  # ⑤ 요청 키워드 → 제외
        "에어컨필터교체": 800,          # ② 미인식 → 제외
        "모닝에어컨필터": 5,            # ③ 검색량<10 → 제외
        "셀토스와이퍼": 300,            # ① 제품 미포함 → 제외
    }
    index = _FakeIndex({adopt_kw: "셀토스", "모닝에어컨필터": "모닝", "셀토스와이퍼": "셀토스"})

    chunks = []
    scan_models(
        ["셀토스"], product,
        volumes_fn=lambda kws: {k: 1000 for k in kws},
        blog_fn=lambda k: 50,
        recent_fn=lambda k: 2,
        on_chunk=chunks.append,
        variants_fn=lambda kws: dict(variants),
        index=index,
        vault_rows=[],
        scanned_at="2026-02-01 09:00",
    )
    rows = [r for c in chunks for r in c]
    kws = {r[3] for r in rows}
    assert req_kw in kws and adopt_kw in kws
    assert kws == {req_kw, adopt_kw}          # 나머지 전부 제외
    adopted = next(r for r in rows if r[3] == adopt_kw)
    assert adopted[2] == "셀토스"              # car_model = 인식 정규명
    assert adopted[1] == product              # product = 현재 스캔 제품
    assert adopted[-1] == "정상"


def test_variant_skipped_when_recent_in_vault():
    """④ 발굴함에 30일 내 행 있으면 채택 안 함."""
    product = "에어컨필터"
    adopt_kw = "셀토스자동차에어컨필터"
    vault_rows = [{
        "keyword": adopt_kw, "scanned_at": "2026-01-28 09:00", "status": "정상",
        "product": product, "car_model": "셀토스", "volume": "500",
    }]
    index = _FakeIndex({adopt_kw: "셀토스"})
    chunks = []
    scan_models(
        ["셀토스"], product,
        volumes_fn=lambda kws: {k: 1000 for k in kws},
        blog_fn=lambda k: 50,
        on_chunk=chunks.append,
        variants_fn=lambda kws: {adopt_kw: 500},
        index=index,
        vault_rows=vault_rows,
        today=datetime.date(2026, 2, 1),   # 4일 전 스캔 → 30일 내 → 제외
        scanned_at="2026-02-01 09:00",
    )
    kws = {r[3] for c in chunks for r in c}
    assert adopt_kw not in kws


def test_variant_cap_stops_adoption():
    """VAULT_VARIANT_CAP 초과 시 채택 중단."""
    product = "에어컨필터"
    variants = {f"셀토스{i}에어컨필터": 500 for i in range(10)}
    index = _FakeIndex({k: "셀토스" for k in variants})
    chunks = []
    scan_models(
        ["셀토스"], product,
        volumes_fn=lambda kws: {k: 1000 for k in kws},
        blog_fn=lambda k: 50,
        on_chunk=chunks.append,
        variants_fn=lambda kws: dict(variants),
        index=index,
        vault_rows=[],
        variant_cap=3,
        scanned_at="2026-02-01 09:00",
    )
    rows = [r for c in chunks for r in c]
    adopted = [r for r in rows if r[3] in variants]
    assert len(adopted) == 3                   # CAP=3 에서 중단


def test_variant_disabled_without_index():
    """variants_fn 만 있고 index 없으면 부수확 비활성(기존 동작)."""
    chunks = []
    scan_models(
        ["셀토스"], "에어컨필터",
        volumes_fn=lambda kws: {k: 1000 for k in kws},
        blog_fn=lambda k: 50,
        on_chunk=chunks.append,
        variants_fn=lambda kws: {"셀토스자동차에어컨필터": 500},
        index=None,
        vault_rows=[],
        scanned_at="2026-02-01 09:00",
    )
    kws = {r[3] for c in chunks for r in c}
    assert kws == {build_keyword("셀토스", "에어컨필터")}   # 변형 없음


# ── v1.1 §4 계약: 배치당 keywordstool API 1회 ────────────────────────────────

def test_make_batch_query_one_api_call_per_batch():
    """volumes_fn·variants_fn 을 같은 배치로 불러도 request_fn 은 배치당 1회."""
    calls = {"n": 0}

    def _request(kws):
        calls["n"] += 1
        return [
            {"relKeyword": "셀토스에어컨필터", "monthlyPcQcCnt": 600, "monthlyMobileQcCnt": 400},
            {"relKeyword": "셀토스자동차에어컨필터", "monthlyPcQcCnt": 300, "monthlyMobileQcCnt": 200},
        ]

    volumes_fn, variants_fn = make_batch_query(_request)
    kws = ["셀토스에어컨필터"]
    vols = volumes_fn(kws)
    vars_ = variants_fn(kws)          # 같은 배치 → 캐시 재사용
    _ = volumes_fn(kws)               # 재호출도 캐시
    assert calls["n"] == 1            # ★배치당 1회
    assert vols["셀토스에어컨필터"] == 1000
    assert vars_["셀토스자동차에어컨필터"] == 500

    volumes_fn(["다른키워드"])         # 다른 배치 → 새 호출
    assert calls["n"] == 2
