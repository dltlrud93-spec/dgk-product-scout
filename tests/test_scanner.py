"""
test_scanner.py — 데이터 전수 스캔 엔진(plan_scan · scan_models) 순수 검증(네트워크 0).
"""

from __future__ import annotations

import datetime

from src.core.jogyeonpyo import build_keyword
from src.core.scanner import plan_scan, scan_models


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
