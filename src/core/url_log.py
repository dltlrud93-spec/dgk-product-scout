"""
url_log.py — 추적 URL 생성 이력을 구글 시트에 적재(보조 기능).

설계 원칙: URL 생성·표시·'제품링크 칸에 넣기'는 ★불변. 이력 저장은 별도 버튼으로만,
실패해도 URL 생성에 영향 0 — 모든 I/O 는 try/except 로 감싸 "saved"/"duplicate"/
"no_config"/"error" 문자열만 반환(절대 raise 안 함). 인증은 jogyeonpyo._authorize 재사용.

순수부(build_log_row·write_log_row·_resolve_log_sheet_id)는 worksheet 유사객체로 테스트.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

_log = logging.getLogger(__name__)

LOG_HEADER = ["생성일시", "제품", "차종", "상품번호", "nt_medium", "nt_detail", "URL"]

_SECRET_LOG_SHEET_ID = "url_log_sheet_id"
_ENV_LOG_SHEET_ID = "URL_LOG_SHEET_ID"


def build_log_row(now_kst, product_code, car, product_no, medium, detail, url) -> list:
    """이력 한 줄을 LOG_HEADER 순서로 만든다(순수). 생성일시=YYYY-MM-DD HH:MM."""
    return [
        now_kst.strftime("%Y-%m-%d %H:%M"),
        product_code, car, product_no, medium, detail, url,
    ]


def _non_empty_rows(vals: list) -> list:
    """완전 빈 행(모든 셀이 공백)은 제외한 '실데이터 행'만 추린다.

    새 구글 시트의 get_all_values() 는 [['']] 같은 빈 칸 한 줄을 주기도 해서,
    이를 '빈 시트'로 올바로 판별하려면 이 정규화가 필요하다."""
    return [v for v in vals if any(str(c).strip() for c in v)]


def write_log_row(ws, row: list, header: list = LOG_HEADER) -> str:
    """worksheet 유사객체에 한 줄 추가(순수 — I/O 예외는 호출부가 처리).

    진짜 빈 시트(또는 빈 칸뿐)면 헤더+행. 데이터는 있는데 헤더가 없으면 헤더를 1행에 삽입
    (기존 깨진 상태 보정). URL열(마지막열) 중복이면 'duplicate', 그 외 append 후 'saved'.
    """
    non_empty = _non_empty_rows(ws.get_all_values())
    if not non_empty:
        ws.append_row(header)
        ws.append_row(row)
        return "saved"
    if non_empty[0] != header:               # 데이터만 있고 헤더 없음 → 보정
        ws.insert_row(header, index=1)
    url = row[-1]
    for existing in non_empty:               # 헤더 유무와 무관하게 URL 마지막열 비교
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
    except Exception:  # noqa: BLE001 — I/O 실패는 URL 생성과 무관, 'error' 반환(raise 안 함)
        _log.exception("append_url_log 실패 sid=%s", sid)   # ★진단용 전체 트레이스백
        return "error"


def fetch_recent_logs(
    n: int = 10, *, sheet_id: Optional[str] = None, creds=None, header: list = LOG_HEADER,
) -> list:
    """최근 이력 n행을 최신순(위가 최신)으로 반환. 실패·빈 결과는 [].

    ★헤더가 1행일 때만 제거하고, 헤더 없는(깨진) 시트는 전체를 데이터로 본다."""
    try:
        sid = _resolve_log_sheet_id(sheet_id)
        if not sid:
            return []
        from src.core.jogyeonpyo import _authorize

        ws = _authorize(creds).open_by_key(sid).sheet1
        non_empty = _non_empty_rows(ws.get_all_values())
        body = non_empty[1:] if (non_empty and non_empty[0] == header) else non_empty
        return list(reversed(body[-n:])) if body else []
    except Exception:  # noqa: BLE001
        _log.exception("fetch_recent_logs 실패")   # ★진단용 전체 트레이스백
        return []
