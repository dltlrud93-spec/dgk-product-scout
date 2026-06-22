"""
url_log.py — 추적 URL 생성 이력을 구글 시트에 적재(보조 기능).

설계 원칙: URL 생성·표시·'제품링크 칸에 넣기'는 ★불변. 이력 저장은 별도 버튼으로만,
실패해도 URL 생성에 영향 0 — 모든 I/O 는 try/except 로 감싸 "saved"/"duplicate"/
"no_config"/"error" 문자열만 반환(절대 raise 안 함). 인증은 jogyeonpyo._authorize 재사용.

순수부(build_log_row·write_log_row·_resolve_log_sheet_id)는 worksheet 유사객체로 테스트.
"""

from __future__ import annotations

import os
from typing import Optional

LOG_HEADER = ["생성일시", "제품", "차종", "상품번호", "nt_medium", "nt_detail", "URL"]

_SECRET_LOG_SHEET_ID = "url_log_sheet_id"
_ENV_LOG_SHEET_ID = "URL_LOG_SHEET_ID"


def build_log_row(now_kst, product_code, car, product_no, medium, detail, url) -> list:
    """이력 한 줄을 LOG_HEADER 순서로 만든다(순수). 생성일시=YYYY-MM-DD HH:MM."""
    return [
        now_kst.strftime("%Y-%m-%d %H:%M"),
        product_code, car, product_no, medium, detail, url,
    ]


def write_log_row(ws, row: list, header: list = LOG_HEADER) -> str:
    """worksheet 유사객체에 한 줄 추가(순수 — I/O 예외는 호출부가 처리).

    빈 시트면 헤더 먼저 추가. URL열(마지막열)에 같은 URL 이 이미 있으면 'duplicate'
    (append 안 함). 그 외 append 후 'saved'.
    """
    vals = ws.get_all_values()
    if not vals:
        ws.append_row(header)
        ws.append_row(row)
        return "saved"
    url = row[-1]
    for existing in vals:
        if existing and existing[-1] == url:
            return "duplicate"
    ws.append_row(row)
    return "saved"


def _resolve_log_sheet_id(sheet_id: Optional[str] = None) -> str:
    """로그 시트 ID 결정. 인자 → st.secrets → env 순. 없으면 "" 반환(조용히)."""
    if sheet_id:
        return sheet_id
    try:
        import streamlit as st  # noqa: PLC0415

        if _SECRET_LOG_SHEET_ID in st.secrets:
            return str(st.secrets[_SECRET_LOG_SHEET_ID])
    except Exception:  # noqa: BLE001 — streamlit 미설치/secrets 없음
        pass
    return os.environ.get(_ENV_LOG_SHEET_ID, "") or ""


def append_url_log(
    *, product_code, car, product_no, medium, detail, url,
    sheet_id: Optional[str] = None, creds=None,
) -> str:
    """이력 1줄을 구글시트에 적재. 반환 'saved'/'duplicate'/'no_config'/'error'(★raise 안 함)."""
    sid = _resolve_log_sheet_id(sheet_id)
    if not sid:
        return "no_config"
    try:
        from datetime import datetime, timedelta, timezone

        from src.core.jogyeonpyo import _authorize  # 인증 재사용

        now = datetime.now(timezone(timedelta(hours=9)))   # KST
        ws = _authorize(creds).open_by_key(sid).sheet1
        return write_log_row(
            ws, build_log_row(now, product_code, car, product_no, medium, detail, url))
    except Exception:  # noqa: BLE001 — I/O 실패는 URL 생성과 무관, 조용히 'error'
        return "error"


def fetch_recent_logs(n: int = 10, *, sheet_id: Optional[str] = None, creds=None) -> list:
    """최근 이력 n행을 최신순(위가 최신)으로 반환. 실패·빈 결과는 []."""
    try:
        sid = _resolve_log_sheet_id(sheet_id)
        if not sid:
            return []
        from src.core.jogyeonpyo import _authorize

        ws = _authorize(creds).open_by_key(sid).sheet1
        vals = ws.get_all_values()
        body = vals[1:]              # 헤더 제외
        return list(reversed(body[-n:])) if body else []
    except Exception:  # noqa: BLE001
        return []
