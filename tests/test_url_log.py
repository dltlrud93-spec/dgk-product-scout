"""
test_url_log.py — 추적 URL 이력 적재 순수부 검증(I/O 없음 — FakeWS 로).
"""

from __future__ import annotations

from datetime import datetime

from src.core.url_log import (
    LOG_HEADER,
    _non_empty_rows,
    _resolve_log_sheet_id,
    build_log_row,
    fetch_recent_logs,
    write_log_row,
)


class FakeWS:
    """worksheet 유사객체 — get_all_values()/append_row()/insert_row()."""

    def __init__(self, rows=None):
        self.rows = [list(r) for r in (rows or [])]

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def append_row(self, r):
        self.rows.append(list(r))

    def insert_row(self, r, index=1):
        self.rows.insert(index - 1, list(r))    # gspread index 는 1-based


class _FakeClient:
    """_authorize 대역 — open_by_key().sheet1 로 FakeWS 반환."""

    def __init__(self, ws):
        self._ws = ws

    def open_by_key(self, _sid):
        return self

    @property
    def sheet1(self):
        return self._ws


def _patch_authorize(monkeypatch, ws):
    monkeypatch.setattr("src.core.jogyeonpyo._authorize", lambda creds=None: _FakeClient(ws))


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


def test_write_log_row_blank_cell_sheet_writes_header():
    """새 시트 get_all_values()=[['']] (빈 칸 한 줄) → 빈 시트로 보고 헤더+행."""
    ws = FakeWS([[""]])
    row = build_log_row(datetime(2026, 6, 22, 9, 5), "와이퍼", "EV5", "1", "N_REVU",
                        "d", "https://u/1")
    assert write_log_row(ws, row) == "saved"
    ne = _non_empty_rows(ws.rows)
    assert ne[0] == LOG_HEADER      # 실데이터 첫 행이 헤더
    assert ne[1] == row
    assert len(ne) == 2


def test_write_log_row_inserts_header_when_missing():
    """헤더 없이 데이터행만 있는(기존 깨진) 시트 → 헤더를 1행에 삽입 후 신규 append."""
    data_only = ["t", "와이퍼", "EV5", "1", "N_REVU", "d", "https://u/1"]
    ws = FakeWS([data_only])
    new = build_log_row(datetime(2026, 6, 22, 9, 7), "에어컨필터", "쏘렌토", "2",
                        "N_REVU", "d2", "https://u/2")
    assert write_log_row(ws, new) == "saved"
    assert ws.rows[0] == LOG_HEADER         # 헤더가 1행에 삽입됨
    assert ws.rows[-1][-1] == "https://u/2"  # 신규 append


def test_fetch_recent_logs_headerless_returns_data(monkeypatch):
    """헤더 없는 [[데이터]] → 데이터 반환(빈 [] 아님)."""
    data = ["t", "와이퍼", "EV5", "1", "N_REVU", "d", "https://u/1"]
    _patch_authorize(monkeypatch, FakeWS([data]))
    out = fetch_recent_logs(10, sheet_id="x")
    assert out == [data]


def test_fetch_recent_logs_strips_header_when_present(monkeypatch):
    """헤더+데이터 → 헤더 제외하고 데이터만, 최신순(위가 최신)."""
    d1 = ["t1", "와이퍼", "EV5", "1", "N_REVU", "d", "https://u/1"]
    d2 = ["t2", "에어컨필터", "쏘렌토", "2", "N_REVU", "d2", "https://u/2"]
    _patch_authorize(monkeypatch, FakeWS([LOG_HEADER, d1, d2]))
    out = fetch_recent_logs(10, sheet_id="x")
    assert out == [d2, d1]      # 헤더 제외 + 역순(최신 위)


def test_resolve_log_sheet_id_arg_wins():
    assert _resolve_log_sheet_id("sheet-abc") == "sheet-abc"


def test_resolve_log_sheet_id_none_when_unset(monkeypatch):
    monkeypatch.delenv("URL_LOG_SHEET_ID", raising=False)
    # st.secrets 접근은 내부 try/except 로 흡수 → "" 반환(예외 없음)
    assert _resolve_log_sheet_id(None) in ("", )
