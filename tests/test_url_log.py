"""
test_url_log.py — 추적 URL 이력 적재 순수부 검증(I/O 없음 — FakeWS 로).
"""

from __future__ import annotations

from datetime import datetime

from src.core.url_log import (
    LOG_HEADER,
    _resolve_log_sheet_id,
    build_log_row,
    write_log_row,
)


class FakeWS:
    """worksheet 유사객체 — get_all_values()/append_row() 만."""

    def __init__(self, rows=None):
        self.rows = [list(r) for r in (rows or [])]

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def append_row(self, r):
        self.rows.append(list(r))


def test_build_log_row_fields_and_order():
    now = datetime(2026, 6, 22, 9, 5)
    row = build_log_row(now, "에어컨필터", "쏘렌토", "9600617781", "N_REVU",
                        "260622_에어컨필터_쏘렌토", "https://x/p?nt_source=a")
    assert len(row) == len(LOG_HEADER) == 7
    assert row[0] == "2026-06-22 09:05"          # 생성일시 포맷
    assert row[1] == "에어컨필터" and row[2] == "쏘렌토"
    assert row[3] == "9600617781" and row[4] == "N_REVU"
    assert row[-1] == "https://x/p?nt_source=a"


def test_write_log_row_empty_sheet_writes_header_then_row():
    ws = FakeWS()
    row = build_log_row(datetime(2026, 6, 22, 9, 5), "와이퍼", "EV5", "1", "N_REVU",
                        "d", "https://u/1")
    assert write_log_row(ws, row) == "saved"
    assert ws.rows[0] == LOG_HEADER               # 헤더 먼저
    assert ws.rows[1] == row                       # 그다음 데이터행
    assert len(ws.rows) == 2


def test_write_log_row_duplicate_url_not_appended():
    existing = [LOG_HEADER, ["t", "와이퍼", "EV5", "1", "N_REVU", "d", "https://u/1"]]
    ws = FakeWS(existing)
    dup = build_log_row(datetime(2026, 6, 22, 9, 6), "와이퍼", "EV5", "1", "N_REVU",
                        "d", "https://u/1")
    before = len(ws.rows)
    assert write_log_row(ws, dup) == "duplicate"
    assert len(ws.rows) == before                  # append 안 일어남


def test_write_log_row_new_url_appends():
    existing = [LOG_HEADER, ["t", "와이퍼", "EV5", "1", "N_REVU", "d", "https://u/1"]]
    ws = FakeWS(existing)
    new = build_log_row(datetime(2026, 6, 22, 9, 7), "에어컨필터", "쏘렌토", "2",
                        "N_REVU", "d2", "https://u/2")
    assert write_log_row(ws, new) == "saved"
    assert len(ws.rows) == 3
    assert ws.rows[-1][-1] == "https://u/2"


def test_resolve_log_sheet_id_arg_wins():
    assert _resolve_log_sheet_id("sheet-abc") == "sheet-abc"


def test_resolve_log_sheet_id_none_when_unset(monkeypatch):
    monkeypatch.delenv("URL_LOG_SHEET_ID", raising=False)
    # st.secrets 접근은 내부 try/except 로 흡수 → "" 반환(예외 없음)
    assert _resolve_log_sheet_id(None) in ("", )
