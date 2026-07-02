"""
test_vault.py — 발굴함 저장소 순수부 검증(I/O 없음 — Fake worksheet/spreadsheet).
"""

from __future__ import annotations

from src.core.vault import (
    VAULT_HEADER,
    append_vault_rows,
    get_or_create_worksheet,
    latest_by_keyword,
    make_vault_row,
    parse_vault_values,
)


class FakeWS:
    """worksheet 유사객체 — get_all_values/append_row/insert_row/append_rows(호출 카운트)."""

    def __init__(self, rows=None):
        self.rows = [list(r) for r in (rows or [])]
        self.append_rows_calls = 0
        self.append_row_calls = 0

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def append_row(self, r):
        self.append_row_calls += 1
        self.rows.append(list(r))

    def insert_row(self, r, index=1):
        self.rows.insert(index - 1, list(r))

    def append_rows(self, rs):
        self.append_rows_calls += 1
        self.rows.extend([list(r) for r in rs])


class FakeSS:
    """spreadsheet 유사객체 — 이름별 워크시트 보관. 없는 이름은 예외(gspread 유사)."""

    def __init__(self, sheets=None):
        self.sheets = dict(sheets or {})
        self.added = []

    def worksheet(self, title):
        if title not in self.sheets:
            raise RuntimeError(f"WorksheetNotFound: {title}")
        return self.sheets[title]

    def add_worksheet(self, title, rows, cols):
        ws = FakeWS()
        self.sheets[title] = ws
        self.added.append((title, rows, cols))
        return ws


# ── 헤더 자동 생성 ────────────────────────────────────────────────────────────

def test_header_is_11_columns():
    assert len(VAULT_HEADER) == 11
    assert VAULT_HEADER[0] == "scanned_at"
    assert VAULT_HEADER[-1] == "status"


def test_get_or_create_makes_worksheet_and_header():
    ss = FakeSS()
    ws = get_or_create_worksheet(ss)
    assert ss.added and ss.added[0][0] == "발굴함"
    assert ws.rows[0] == VAULT_HEADER


def test_get_or_create_appends_header_to_empty_existing():
    """빈 시트가 이미 있으면 add 하지 않고 헤더만 기록."""
    ss = FakeSS({"발굴함": FakeWS()})
    ws = get_or_create_worksheet(ss)
    assert not ss.added
    assert ws.rows[0] == VAULT_HEADER


def test_get_or_create_inserts_header_when_missing():
    """헤더 없이 데이터만 있는 시트 → 1행에 헤더 삽입."""
    data = ["2026-01-01 09:00", "에어컨필터", "셀토스", "셀토스에어컨필터", 100]
    ss = FakeSS({"발굴함": FakeWS([data])})
    ws = get_or_create_worksheet(ss)
    assert ws.rows[0] == VAULT_HEADER
    assert ws.rows[1] == data


def test_get_or_create_keeps_existing_header():
    ss = FakeSS({"발굴함": FakeWS([VAULT_HEADER])})
    ws = get_or_create_worksheet(ss)
    assert ws.rows == [VAULT_HEADER]   # 그대로


# ── append 는 청크당 append_rows 1회 ─────────────────────────────────────────

def test_append_vault_rows_single_batch_call():
    ws = FakeWS([VAULT_HEADER])
    chunk = [
        make_vault_row("2026-01-01 09:00", "에어컨필터", "셀토스", "셀토스에어컨필터", 100,
                       doc_count=50, ratio=0.5, grade="🟡 황금", recent_3m=3,
                       opportunity_score=666, status="정상"),
        make_vault_row("2026-01-01 09:00", "에어컨필터", "모닝", "모닝에어컨필터", 5, status="잠복"),
    ]
    append_vault_rows(ws, chunk)
    assert ws.append_rows_calls == 1        # ★행당 아님 — 묶음 1회
    assert ws.append_row_calls == 0
    assert len(ws.rows) == 3                 # 헤더 + 2행


def test_append_vault_rows_empty_is_noop():
    ws = FakeWS([VAULT_HEADER])
    append_vault_rows(ws, [])
    assert ws.append_rows_calls == 0


# ── parse_vault_values ────────────────────────────────────────────────────────

def test_parse_vault_values_strips_header_and_maps_keys():
    row = ["2026-01-01 09:00", "에어컨필터", "셀토스", "셀토스에어컨필터", "100",
           "50", "0.5", "🟡 황금", "3", "666", "정상"]
    out = parse_vault_values([VAULT_HEADER, row])
    assert len(out) == 1
    assert out[0]["keyword"] == "셀토스에어컨필터"
    assert out[0]["status"] == "정상"
    assert out[0]["scanned_at"] == "2026-01-01 09:00"


def test_parse_vault_values_empty_returns_empty():
    assert parse_vault_values([]) == []
    assert parse_vault_values([[""]]) == []


def test_parse_vault_values_short_row_fills_blanks():
    """셀이 부족한 잠복 행(뒤 지표 공란)도 모든 키를 가진다."""
    short = ["2026-01-01 09:00", "에어컨필터", "모닝", "모닝에어컨필터", "5"]
    out = parse_vault_values([VAULT_HEADER, short])
    assert out[0]["doc_count"] == ""
    assert out[0]["status"] == ""


# ── latest_by_keyword ─────────────────────────────────────────────────────────

def _row(keyword, scanned_at, status="정상", volume=100):
    return {"keyword": keyword, "scanned_at": scanned_at, "status": status, "volume": volume}


def test_latest_by_keyword_picks_latest_and_prev():
    rows = [
        _row("셀토스에어컨필터", "2026-01-01 09:00", status="잠복", volume=5),
        _row("셀토스에어컨필터", "2026-02-01 09:00", status="정상", volume=120),
        _row("모닝에어컨필터", "2026-01-15 09:00"),
    ]
    m = latest_by_keyword(rows)
    latest, prev = m["셀토스에어컨필터"]
    assert latest["scanned_at"] == "2026-02-01 09:00"   # 최신
    assert prev["scanned_at"] == "2026-01-01 09:00"      # 직전
    assert prev["status"] == "잠복"                        # 수요형성 감지에 쓰일 직전 상태


def test_latest_by_keyword_single_has_no_prev():
    m = latest_by_keyword([_row("모닝에어컨필터", "2026-01-15 09:00")])
    latest, prev = m["모닝에어컨필터"]
    assert prev is None


def test_latest_by_keyword_skips_blank_keyword():
    m = latest_by_keyword([_row("", "2026-01-15 09:00")])
    assert m == {}
