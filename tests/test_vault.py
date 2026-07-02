"""
test_vault.py — 발굴함 저장소 순수부 검증(I/O 없음 — Fake worksheet/spreadsheet).
"""

from __future__ import annotations

from src.core.vault import (
    VAULT_HEADER,
    append_vault_rows,
    detect_demand_formation,
    executed_keywords_from_logs,
    filter_latest_rows,
    get_or_create_worksheet,
    group_vault_rows,
    latest_by_keyword,
    latest_rows,
    make_vault_row,
    new_keywords,
    parse_vault_values,
    summarize_vault,
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


# ── 뷰 로직: 칩·수요형성·NEW·필터·그룹핑 ──────────────────────────────────────

def _drow(keyword, product="에어컨필터", status="정상", grade="🟡 황금",
          scanned_at="2026-02-01 09:00", volume="1000", opp="500", doc="50",
          ratio="0.05", recent="3"):
    return {
        "scanned_at": scanned_at, "product": product, "car_model": keyword,
        "keyword": keyword, "volume": volume, "doc_count": doc, "ratio": ratio,
        "grade": grade, "recent_3m": recent, "opportunity_score": opp, "status": status,
    }


def test_summarize_vault_counts_by_bucket():
    rows = [
        _drow("a", grade="🟡 황금"),
        _drow("b", grade="🟢 해볼만"),
        _drow("c", grade="🔴 포화/후순위"),
        _drow("d", status="잠복", grade="", opp="", doc="", ratio="", recent=""),
    ]
    s = summarize_vault(rows, executed=frozenset({"a"}))
    assert s["total"] == 4
    assert s["gold"] == 1 and s["ok"] == 1 and s["saturated"] == 1 and s["dormant"] == 1
    assert s["executed"] == 1


def test_detect_demand_formation_latest_normal_prev_dormant():
    rows = [
        _drow("셀토스에어컨필터", status="잠복", grade="", scanned_at="2026-01-01 09:00", volume="5"),
        _drow("셀토스에어컨필터", status="정상", grade="🟡 황금", scanned_at="2026-02-01 09:00", volume="120"),
        _drow("모닝에어컨필터", status="정상", scanned_at="2026-02-01 09:00"),  # 직전 없음
    ]
    formed = detect_demand_formation(rows)
    assert formed == [("셀토스에어컨필터", "120")]


def test_new_keywords_only_single_appearance():
    rows = [
        _drow("a", scanned_at="2026-01-01 09:00"),
        _drow("a", scanned_at="2026-02-01 09:00"),  # 2회 → NEW 아님
        _drow("b", scanned_at="2026-02-01 09:00"),  # 1회 → NEW
    ]
    assert new_keywords(rows) == {"b"}


def test_filter_latest_rows_exclude_executed():
    rows = [_drow("a"), _drow("b")]
    out = filter_latest_rows(rows, exclude_executed=True, executed=frozenset({"a"}))
    assert [r["keyword"] for r in out] == ["b"]


def test_filter_latest_rows_by_product():
    rows = [_drow("a", product="에어컨필터"), _drow("b", product="와이퍼")]
    out = filter_latest_rows(rows, product="와이퍼")
    assert [r["keyword"] for r in out] == ["b"]
    assert filter_latest_rows(rows, product="전체") == rows  # '전체'는 필터 없음


def test_group_vault_rows_orders_gold_by_opportunity_desc():
    rows = [
        _drow("low", grade="🟡 황금", opp="100"),
        _drow("high", grade="🟡 황금", opp="900"),
        _drow("dorm", status="잠복", grade="", opp=""),
    ]
    gold, ok, saturated, dormant = group_vault_rows(rows)
    assert [r["keyword"] for r in gold] == ["high", "low"]   # 기회 점수 내림차순
    assert len(dormant) == 1


def test_latest_rows_uses_latest_only():
    rows = [
        _drow("a", scanned_at="2026-01-01 09:00", volume="5", status="잠복", grade=""),
        _drow("a", scanned_at="2026-02-01 09:00", volume="120", status="정상"),
    ]
    lr = latest_rows(rows)
    assert len(lr) == 1
    assert lr[0]["volume"] == "120"   # 최신 행


# ── 집행됨: URL 이력 (제품·차종) → build_keyword 재구성 ────────────────────────

def test_executed_keywords_from_logs_reconstructs_keyword():
    # LOG_HEADER: 생성일시|제품|차종|상품번호|... — product_idx=1, car_idx=2
    logs = [
        ["2026-06-22 09:05", "에어컨필터", "셀토스", "1", "N_REVU", "d", "https://u/1"],
        ["2026-06-22 09:06", "와이퍼", "그랑 콜레오스", "2", "N_REVU", "d", "https://u/2"],
    ]
    got = executed_keywords_from_logs(logs)
    assert "셀토스에어컨필터" in got
    assert "그랑콜레오스와이퍼" in got   # 정규화(내부 공백 제거) 후 build_keyword 와 동일


def test_executed_keywords_skips_header_and_blank():
    logs = [
        ["생성일시", "제품", "차종", "상품번호", "nt_medium", "nt_detail", "URL"],  # 헤더
        ["t", "", "", "", "", "", ""],                                              # 빈행
    ]
    assert executed_keywords_from_logs(logs) == set()
