"""
vault.py — 황금 발굴함 저장소(구글시트 '발굴함' 탭).

데이터 전수 스캐너(scanner.py)가 만든 스캔 결과를 URL 이력과 같은 구글시트 문서의
'발굴함' 워크시트에 영구 적재한다. 신규 secrets/공유 없이 url_log 의 시트 ID 해석과
jogyeonpyo 의 서비스계정 인증을 그대로 재사용한다.

저장 원칙(중단 내성):
  · 청크(차종 N개)마다 append_vault_rows 로 시트에 즉시 기록 — 스캔이 중간에 끊겨도 진행분 보존.
  · append 는 청크당 append_rows 1회(행당 호출 금지).
  · 조회 실패한 키워드는 행을 만들지 않는다 → 시트에 행이 없으므로 다음 실행 plan_scan 이 자동 재포함.

헤더(11컬럼 고정):
  scanned_at | product | car_model | keyword | volume | doc_count | ratio | grade |
  recent_3m | opportunity_score | status

순수부(parse_vault_values·latest_by_keyword·get_or_create_worksheet·append_vault_rows)는
worksheet/spreadsheet 유사객체로 네트워크 없이 테스트한다.
"""

from __future__ import annotations

import logging
from typing import Optional

from src.core.url_log import _non_empty_rows, _resolve_log_sheet_id

_log = logging.getLogger(__name__)

VAULT_WORKSHEET = "발굴함"

# 11컬럼 고정 — 순서·개수 불변(scanner 가 이 순서로 행을 만든다).
VAULT_HEADER = [
    "scanned_at",
    "product",
    "car_model",
    "keyword",
    "volume",
    "doc_count",
    "ratio",
    "grade",
    "recent_3m",
    "opportunity_score",
    "status",
]


def make_vault_row(
    scanned_at,
    product,
    car_model,
    keyword,
    volume,
    doc_count="",
    ratio="",
    grade="",
    recent_3m="",
    opportunity_score="",
    status="정상",
) -> list:
    """VAULT_HEADER 순서의 한 행(list)을 만든다(순수).

    잠복(검색량<10) 행은 doc_count/ratio/grade/recent_3m/opportunity_score 를 공란("")으로 둔다.
    """
    return [
        scanned_at, product, car_model, keyword, volume,
        doc_count, ratio, grade, recent_3m, opportunity_score, status,
    ]


def get_or_create_worksheet(spreadsheet, title: str = VAULT_WORKSHEET, header: list = VAULT_HEADER):
    """Spreadsheet 유사객체에서 title 워크시트를 얻거나(없으면) 만들고 헤더를 보장한다.

    · 워크시트가 없으면 add_worksheet 후 헤더 1행 기록.
    · 있으면: 완전 빈 시트면 헤더 append, 헤더 없이 데이터만 있으면 1행에 헤더 삽입(보정).
    반환: worksheet 유사객체.
    """
    try:
        ws = spreadsheet.worksheet(title)
    except Exception:  # noqa: BLE001 — gspread WorksheetNotFound 등(신규 생성 경로)
        ws = spreadsheet.add_worksheet(title=title, rows=1000, cols=len(header))
        ws.append_row(list(header))
        return ws
    non_empty = _non_empty_rows(ws.get_all_values())
    if not non_empty:
        ws.append_row(list(header))
    elif non_empty[0] != header:
        ws.insert_row(list(header), index=1)
    return ws


def append_vault_rows(ws, rows: list) -> None:
    """청크(list of list)를 append_rows 1회로 기록(순수 — I/O 예외는 호출부가 처리).

    빈 rows 는 무시(호출 0). ★행당 호출 금지 — 반드시 묶음 1회.
    """
    if not rows:
        return
    ws.append_rows([list(r) for r in rows])


def parse_vault_values(values: list) -> list[dict]:
    """get_all_values() 결과(2차원 리스트) → 행 dict 리스트(순수).

    헤더 행(VAULT_HEADER 일치)은 제외. 각 dict 는 VAULT_HEADER 키를 모두 가진다(부족 셀은 "").
    헤더가 없는(깨진) 시트는 전체를 데이터로 본다.
    """
    non_empty = _non_empty_rows(values)
    if not non_empty:
        return []
    body = non_empty[1:] if non_empty[0] == VAULT_HEADER else non_empty
    out: list[dict] = []
    for row in body:
        out.append({h: (row[i] if i < len(row) else "") for i, h in enumerate(VAULT_HEADER)})
    return out


def latest_by_keyword(rows: list[dict]) -> dict:
    """키워드별 (최신행, 직전행|None) 맵 — 수요형성(잠복→정상) 감지용.

    scanned_at('YYYY-MM-DD HH:MM', 고정폭) 사전순 정렬로 최신·직전을 고른다.
    키워드가 1회만 등장하면 직전은 None. 빈 키워드 행은 제외.
    """
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        kw = str(r.get("keyword", "")).strip()
        if not kw:
            continue
        grouped.setdefault(kw, []).append(r)
    out: dict = {}
    for kw, group in grouped.items():
        ordered = sorted(group, key=lambda r: str(r.get("scanned_at", "")))
        latest = ordered[-1]
        prev = ordered[-2] if len(ordered) >= 2 else None
        out[kw] = (latest, prev)
    return out


# ── I/O 진입점(app 층에서 사용) — 인증·시트 열기 ─────────────────────────────

def open_vault_worksheet(*, sheet_id: Optional[str] = None, creds=None):
    """'발굴함' 워크시트를 연다(없으면 생성 + 헤더). 시트 ID 미설정이면 None.

    시트 ID 는 url_log 와 동일 문서(url_log_sheet_id/URL_LOG_SHEET_ID), 인증은 jogyeonpyo._authorize.
    """
    sid = _resolve_log_sheet_id(sheet_id)
    if not sid:
        return None
    from src.core.jogyeonpyo import _authorize  # 인증 재사용(지연 import)

    ss = _authorize(creds).open_by_key(sid)
    return get_or_create_worksheet(ss)


def read_vault(*, sheet_id: Optional[str] = None, creds=None) -> list[dict]:
    """발굴함 전체 행을 dict 리스트로 읽는다. 시트 미설정이면 []. I/O 예외는 호출부 처리."""
    ws = open_vault_worksheet(sheet_id=sheet_id, creds=creds)
    if ws is None:
        return []
    return parse_vault_values(ws.get_all_values())
