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


# ═══════════════════════════════════════════════════════════════════════════════
# 집행 체크 — 수동 판정(발굴함과 같은 문서의 '집행체크' 워크시트, append-only 로그)
# ═══════════════════════════════════════════════════════════════════════════════
# URL 이력 자동 판정을 폐지하고 사용자가 직접 체크/해제한다. append-only 인 이유:
#   파괴적 재작성(셀 덮어쓰기·삭제) 금지 · 변경 이력 보존 — 발굴함 탭과 동일 규약.
#   최종 상태는 fold_exec_checks 가 '키워드별 마지막 action' 으로 접어 계산한다.

EXEC_WORKSHEET = "집행체크"
EXEC_HEADER = ["keyword", "action", "at"]   # action ∈ "체크"|"해제", at = now_kst_str()


def open_exec_worksheet(*, sheet_id: Optional[str] = None, creds=None):
    """'집행체크' 워크시트를 연다(없으면 생성 + 헤더). 시트 ID 미설정이면 None(발굴함 탭과 동일 패턴)."""
    sid = _resolve_log_sheet_id(sheet_id)
    if not sid:
        return None
    from src.core.jogyeonpyo import _authorize  # 인증 재사용(지연 import)

    ss = _authorize(creds).open_by_key(sid)
    return get_or_create_worksheet(ss, EXEC_WORKSHEET, EXEC_HEADER)


def parse_exec_values(values: list) -> list[dict]:
    """get_all_values() → 집행체크 행 dict 리스트(순수). 헤더 행 제외, 부족 셀은 ''."""
    non_empty = _non_empty_rows(values)
    if not non_empty:
        return []
    body = non_empty[1:] if non_empty[0] == EXEC_HEADER else non_empty
    return [
        {h: (row[i] if i < len(row) else "") for i, h in enumerate(EXEC_HEADER)}
        for row in body
    ]


def read_exec_checks(*, sheet_id: Optional[str] = None, creds=None) -> list[dict]:
    """집행체크 로그 전체 행을 dict 리스트로 읽는다. 시트 미설정이면 []. I/O 예외는 호출부 처리."""
    ws = open_exec_worksheet(sheet_id=sheet_id, creds=creds)
    if ws is None:
        return []
    return parse_exec_values(ws.get_all_values())


def fold_exec_checks(rows: list[dict]) -> set:
    """append-only 로그 → 현재 '체크됨' 키워드 집합(순수).

    ★행 순서 = 시간순(append-only) 이므로 키워드별 '마지막 행의 action' 이 최종 상태.
    마지막 action 이 "체크" 인 키워드만 집합에 포함(해제로 끝나면 제외). 빈 키워드는 무시.
    """
    state: dict[str, str] = {}
    for r in rows:
        kw = str(r.get("keyword", "")).strip()
        if not kw:
            continue
        state[kw] = str(r.get("action", "")).strip()
    return {kw for kw, action in state.items() if action == "체크"}


def diff_exec_checks(before: set, after: set) -> list:
    """before→after 체크 변경 목록(정렬). 추가=(kw,"체크"), 해제=(kw,"해제"). 무변경이면 []."""
    changes = [(kw, "체크") for kw in (after - before)]
    changes += [(kw, "해제") for kw in (before - after)]
    return sorted(changes)


def append_exec_checks(ws, changes: list, at: Optional[str] = None) -> None:
    """변경 목록 [(keyword, action), ...] → 집행체크 워크시트에 ★append_rows 1회(행당 호출 금지).

    at 미지정 시 now_kst_str() 로 전체 변경에 같은 시각을 찍는다(1회 저장 = 1 시각).
    빈 changes 는 무시(호출 0).
    """
    if not changes:
        return
    if at is None:
        from src.core.teamp_mode import now_kst_str  # 지연 import

        at = now_kst_str()
    ws.append_rows([[kw, action, at] for kw, action in changes])


# ═══════════════════════════════════════════════════════════════════════════════
# 발굴함 뷰 순수 로직 — 칩 계산·수요형성·NEW·그룹핑·집행됨(전부 최신 행 기준)
# ═══════════════════════════════════════════════════════════════════════════════

def _to_int(v, default: Optional[int] = None) -> Optional[int]:
    """시트 셀(문자열)을 정수로. 빈 값·파싱 불가는 default."""
    try:
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return default


def format_recent_int(v) -> str:
    """시트에 저장된 recent_3m(문자열/공란) → 타겟 화면과 동일한 신호등 라벨.

    공란·파싱 불가(미조회·잠복)는 '—'. 숫자면 teamp 의 format_recent_3m 재사용(문법 일치).
    """
    n = _to_int(v, None)
    if n is None:
        return "—"
    from src.core.teamp_mode import format_recent_3m  # 지연 import(순환 방지)

    return format_recent_3m(n)


def latest_rows(rows: list[dict]) -> list[dict]:
    """키워드별 최신 행만 추린 리스트(뷰의 모든 수치는 이 기준으로 일관 계산)."""
    return [latest for latest, _prev in latest_by_keyword(rows).values()]


def keyword_counts(rows: list[dict]) -> dict:
    """키워드별 발굴함 등장 횟수(NEW 배지 판정용)."""
    counts: dict[str, int] = {}
    for r in rows:
        kw = str(r.get("keyword", "")).strip()
        if kw:
            counts[kw] = counts.get(kw, 0) + 1
    return counts


def new_keywords(rows: list[dict]) -> set:
    """발굴함에 1번만 등장한(첫 스캔) 키워드 집합 — NEW 배지."""
    return {kw for kw, c in keyword_counts(rows).items() if c == 1}


def executed_keywords_from_logs(log_rows: list, product_idx: int = 1, car_idx: int = 2) -> set:
    """URL 이력 행들에서 집행된 발굴함 키워드 집합을 재구성.

    URL 이력 시트에 '키워드' 컬럼은 없지만 (제품, 차종) 구조화 컬럼이 있어,
    발굴함 키워드를 만든 것과 동일한 build_keyword(차종, 제품) 로 같은 문자열을
    결정적으로 재구성한다(억지 URL 파싱 없이 신뢰 가능한 매칭). 헤더 행은 건너뜀.
    """
    from src.core.jogyeonpyo import build_keyword  # 지연 import(순환 방지)

    out: set = set()
    for row in log_rows:
        if len(row) <= max(product_idx, car_idx):
            continue
        product = str(row[product_idx]).strip()
        car = str(row[car_idx]).strip()
        if not product or not car or product == "제품":  # 헤더/빈행 skip
            continue
        kw = build_keyword(car, product)
        if kw:
            out.add(kw)
    return out


def _bucket_of(r: dict) -> str:
    """최신 행 → 'gold'/'ok'/'saturated'/'dormant' 버킷(status+grade 기준)."""
    if str(r.get("status", "")).strip() == "잠복":
        return "dormant"
    grade = str(r.get("grade", ""))
    if grade.startswith("🟡"):
        return "gold"
    if grade.startswith("🟢"):
        return "ok"
    return "saturated"


def summarize_vault(rows: list[dict], executed: frozenset = frozenset()) -> dict:
    """최신 행 리스트 → 요약 칩 카운트.

    반환: {total, gold, ok, saturated, dormant, executed}. rows 는 latest_rows() 결과.
    """
    counts = {"total": len(rows), "gold": 0, "ok": 0, "saturated": 0, "dormant": 0, "executed": 0}
    for r in rows:
        counts[_bucket_of(r)] += 1
        if str(r.get("keyword", "")).strip() in executed:
            counts["executed"] += 1
    return counts


def detect_demand_formation(rows: list[dict]) -> list:
    """최신=정상 & 직전=잠복 → 수요 형성. (keyword, volume) 리스트(원본 전체 행 입력)."""
    out: list = []
    for kw, (latest, prev) in latest_by_keyword(rows).items():
        if prev is None:
            continue
        if (str(latest.get("status", "")).strip() == "정상"
                and str(prev.get("status", "")).strip() == "잠복"):
            out.append((kw, str(latest.get("volume", "")).strip()))
    return out


def filter_latest_rows(
    rows: list[dict],
    *,
    product: Optional[str] = None,
    exclude_executed: bool = False,
    executed: frozenset = frozenset(),
) -> list[dict]:
    """최신 행 리스트를 제품·집행됨 조건으로 거른다(순수).

    product None/'전체' 면 제품 필터 없음. exclude_executed=True 면 executed 키워드 제거.
    """
    out: list[dict] = []
    for r in rows:
        if product and product != "전체" and str(r.get("product", "")).strip() != product:
            continue
        if exclude_executed and str(r.get("keyword", "")).strip() in executed:
            continue
        out.append(r)
    return out


def group_vault_rows(rows: list[dict]) -> tuple[list, list, list, list]:
    """최신 행 → (gold, ok, saturated, dormant). gold/ok/saturated 는 기회 점수 내림차순."""
    gold: list = []
    ok: list = []
    saturated: list = []
    dormant: list = []
    for r in rows:
        {"gold": gold, "ok": ok, "saturated": saturated, "dormant": dormant}[_bucket_of(r)].append(r)

    def _opp(r):
        v = _to_int(r.get("opportunity_score"), None)
        return v if v is not None else -1

    gold.sort(key=_opp, reverse=True)
    ok.sort(key=_opp, reverse=True)
    saturated.sort(key=_opp, reverse=True)
    return gold, ok, saturated, dormant
