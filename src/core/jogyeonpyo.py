"""
jogyeonpyo.py — 구글시트 조견표 리더 + 차종명 정규화 (체험단 키워드 자동 생성용).

배경:
  주문가공기(dgk-growth-tool)가 이미 구글시트를 gspread 서비스계정으로 읽고 있다.
  그 인증 방식(core/sheet_io._authorize)을 **그대로 재사용**해 product-scout 에서도
  조견표를 읽는다. 인증 정보(서비스계정 private key)는 절대 코드/깃에 박지 않고
  st.secrets 에서만 읽는다(주문가공기와 동일 키 이름 'gcp_service_account').

조견표 시트 구조(에어컨필터 탭):
  브랜드 | 차종 | 상세차량명 | 연식 | A-품번 | P-품번 | 비고
  → 이 모듈은 '차종' 컬럼만 읽어 차종 목록을 만든다(중복·공백 제거).

차종명 → 검색 키워드 변환(정규화):
  · 괄호·괄호내용 제거 (체험단 _query_name 과 동일 사상: "모닝(세대미상)"→"모닝").
  · 내부 공백 제거 ("그랑 콜레오스" → "그랑콜레오스").
  · 앞뒤 공백 strip.
  → 키워드 = "{정규화차종} 에어컨필터" (검색량·블로그 문서수 쿼리에 100% 동일 문자열 사용).

★이 모듈은 '읽기 전용'이다. 조견표/주문가공기 시트에 절대 쓰지 않는다.
"""

from __future__ import annotations

import json as _json
import os
import re
from typing import Optional, Union

import gspread

# ── 설정 (시트 식별자는 비밀이 아님 — secrets/env/상수 순으로 읽되 하드코딩 기본값 비움) ──
#   인증 private key 와 달리 스프레드시트 ID 는 식별자라 secrets 강제는 아니지만,
#   아직 ID 미확정이므로 st.secrets['jogyeonpyo_sheet_id'] → env → 빈 상수 순으로 읽는다.
DEFAULT_WORKSHEET = "에어컨필터"   # 이번 테스트 대상 탭 (와이퍼_전면/후면은 다음 단계)
DEFAULT_CAR_COL = "차종"          # 차종 컬럼 헤더명 (시트 구조상 B열)
_ENV_SHEET_ID = "JOGYEONPYO_SHEET_ID"
_SECRET_SHEET_ID_KEY = "jogyeonpyo_sheet_id"

# 로컬 폴백: 주문가공기 서비스계정 파일(있으면) — Streamlit Cloud 에선 st.secrets 가 우선.
_LOCAL_SA_FALLBACK = os.path.join(
    os.path.expanduser("~"),
    "Documents", "dgk-growth-tool", "secrets", "service_account.json",
)


class JogyeonpyoConfigError(RuntimeError):
    """조견표 시트 ID 또는 서비스계정 인증 정보가 없을 때."""


# ── 인증 (주문가공기 core/sheet_io._authorize 와 동일 패턴) ──────────────────

def _authorize(creds_path_or_dict: Union[str, dict, None] = None) -> gspread.Client:
    """service account 인증 후 gspread 클라이언트 반환.

    우선순위(주문가공기와 동일):
      1. st.secrets['gcp_service_account'] (TOML 테이블)
      2. st.secrets['gcp_service_account_json'] (JSON 문자열)
      3. dict 직접 전달
      4. str 경로 전달
      5. 로컬 폴백 파일(주문가공기 service_account.json)
    """
    # 1·2: Streamlit Cloud secrets
    try:
        import streamlit as st  # noqa: PLC0415

        if "gcp_service_account" in st.secrets:
            return gspread.service_account_from_dict(dict(st.secrets["gcp_service_account"]))
        if "gcp_service_account_json" in st.secrets:
            return gspread.service_account_from_dict(
                _json.loads(st.secrets["gcp_service_account_json"])
            )
    except Exception:  # noqa: BLE001  (streamlit 미설치/secrets 없음 → 폴백)
        pass

    # 3·4: 직접 전달
    if isinstance(creds_path_or_dict, dict):
        return gspread.service_account_from_dict(creds_path_or_dict)
    if isinstance(creds_path_or_dict, str) and creds_path_or_dict:
        return gspread.service_account(filename=creds_path_or_dict)

    # 5: env JSON / 로컬 폴백 파일
    env_json = os.environ.get("GCP_SERVICE_ACCOUNT_JSON")
    if env_json:
        return gspread.service_account_from_dict(_json.loads(env_json))
    if os.path.exists(_LOCAL_SA_FALLBACK):
        return gspread.service_account(filename=_LOCAL_SA_FALLBACK)

    raise JogyeonpyoConfigError(
        "서비스계정 인증 정보를 찾을 수 없습니다. "
        "st.secrets['gcp_service_account'] 를 설정하거나(권장), "
        f"로컬 파일 {_LOCAL_SA_FALLBACK} 을 두세요."
    )


def _resolve_sheet_id(sheet_id: Optional[str] = None) -> str:
    """조견표 스프레드시트 ID 결정. 인자 → st.secrets → env 순. 없으면 명확한 예외."""
    if sheet_id:
        return sheet_id
    try:
        import streamlit as st  # noqa: PLC0415

        if _SECRET_SHEET_ID_KEY in st.secrets:
            return str(st.secrets[_SECRET_SHEET_ID_KEY])
    except Exception:  # noqa: BLE001
        pass
    env_id = os.environ.get(_ENV_SHEET_ID)
    if env_id:
        return env_id
    raise JogyeonpyoConfigError(
        "조견표 스프레드시트 ID 가 없습니다. "
        f"st.secrets['{_SECRET_SHEET_ID_KEY}'] 또는 환경변수 {_ENV_SHEET_ID} 에 "
        "조견표 시트 ID(URL 의 /d/<여기>/edit)를 설정하세요."
    )


def open_jogyeonpyo(
    creds: Union[str, dict, None] = None,
    sheet_id: Optional[str] = None,
) -> gspread.Spreadsheet:
    """조견표 스프레드시트(읽기 전용 용도)를 연다."""
    return _authorize(creds).open_by_key(_resolve_sheet_id(sheet_id))


# ── 차종명 정규화 → 검색 키워드 ──────────────────────────────────────────────

def normalize_car_keyword(name: str) -> str:
    """조견표 차종명 → 네이버 검색용 정규화 차종명.

    · 괄호·괄호내용 제거: "콜레오스(QM6)" → "콜레오스" (체험단 _query_name 과 동일).
    · 내부 공백 제거:     "그랑 콜레오스" → "그랑콜레오스".
    · 앞뒤 strip.

    예) "그랑 콜레오스" → "그랑콜레오스"
        "셀토스"        → "셀토스"
        "QM6 (콰트로)"  → "QM6"
    """
    s = re.sub(r"\(.*?\)", "", str(name or ""))   # 괄호 제거
    s = re.sub(r"\s+", "", s)                       # 내부 공백 제거
    return s.strip()


def build_keyword(name: str, product: str = "에어컨필터") -> str:
    """차종명 + 제품 → 검색 키워드(공백 없음). 정규화 차종이 비면 빈 문자열(스킵 대상).

    형태: "{정규화차종}{product}" — 예 "그랑콜레오스에어컨필터".
    ★공백 없는 단일 토큰인 이유(둘 다 충족해야 함):
      · 검색광고 키워드도구 hintKeywords 는 공백 포함 구문을 거부한다(400 BAD_REQUEST).
      · 기존 체험단 키워드 단위 규약 = 연관어 원문(예 "셀토스에어컨필터", 공백 없음)으로
        검색량·블로그 문서수를 '동일 문자열'로 잰다(teamp_mode 주석 참조).
    → 검색량 쿼리 = 블로그 쿼리 = 이 한 문자열, 100% 동일.
    """
    car = normalize_car_keyword(name)
    if not car:
        return ""
    return f"{car}{product}".strip()


# ── 조견표 차종 목록 읽기 ────────────────────────────────────────────────────

def _find_header(values: list[list[str]], col_header: str) -> tuple[int, int]:
    """상단 몇 행에서 col_header(예 '차종')가 있는 헤더 행·열 인덱스를 찾는다.

    못 찾으면 (0, 1) 폴백 — 시트 구조상 1행 헤더, 차종은 B열(인덱스 1).
    """
    for i, row in enumerate(values[:5]):
        cells = [str(c).strip() for c in row]
        if col_header in cells:
            return i, cells.index(col_header)
    return 0, 1


def extract_models(
    values: list[list[str]],
    car_col_header: str = DEFAULT_CAR_COL,
    limit: Optional[int] = None,
) -> list[str]:
    """get_all_values() 결과(2차원 리스트) → 차종 목록(중복·공백 제거, 시트 순서 유지).

    순수 함수(네트워크 없음) — 헤더 탐지·차종 컬럼 추출 로직만 담아 오프라인 테스트 가능.
    """
    if not values:
        return []
    header_row, col_idx = _find_header(values, car_col_header)
    models: list[str] = []
    seen: set[str] = set()
    for row in values[header_row + 1:]:
        if len(row) <= col_idx:
            continue
        name = str(row[col_idx]).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        models.append(name)
        if limit is not None and len(models) >= limit:
            break
    return models


def read_car_models(
    *,
    worksheet: str = DEFAULT_WORKSHEET,
    car_col_header: str = DEFAULT_CAR_COL,
    creds: Union[str, dict, None] = None,
    sheet_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[str]:
    """조견표 worksheet 의 '차종' 컬럼을 읽어 차종 목록 반환(중복·공백 제거, 시트 순서 유지).

    Args:
        worksheet:      탭 이름 (기본 '에어컨필터').
        car_col_header: 차종 컬럼 헤더명 (기본 '차종').
        limit:          앞에서 N개만 (소량 테스트용). None=전체.
    """
    ss = open_jogyeonpyo(creds, sheet_id)
    ws = ss.worksheet(worksheet)
    return extract_models(ws.get_all_values(), car_col_header, limit)
