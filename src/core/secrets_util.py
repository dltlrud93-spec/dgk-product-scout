"""
secrets_util.py — 비밀값 우선순위 해석(플레이스홀더 회피). 순수 함수(테스트 가능).

배경(1단계 함정): st.secrets 에 처음 접근하면 Streamlit 이 .streamlit/secrets.toml 의
최상위 키를 os.environ 에 export 한다. 로컬 secrets.toml 의 네이버 키가 플레이스홀더
("your_ad_api_key_here")면 load_dotenv()로 넣어둔 '진짜' .env 키를 덮어써 403 발생.

대책: 여러 출처(.env 스냅샷 / st.secrets / os.environ)의 후보 중 '진짜처럼 보이는' 첫 값을
고른다. 플레이스홀더는 건너뛴다. 네이버 키 브리지에만 적용하고, gcp_service_account(조견표)는
st.secrets 에서만 읽어(jogyeonpyo.py) 서로 충돌하지 않게 분리한다.
"""

from __future__ import annotations

from typing import Iterable, Optional

# 템플릿/예시 값에서 흔한 토큰. 하나라도 (대소문자 무시) 포함되면 플레이스홀더로 간주.
_PLACEHOLDER_SUBSTRINGS = (
    "your_",
    "_here",
    "changeme",
    "example",
    "placeholder",
    "xxxx",
    "<",          # "<your key>" 류
)


def looks_like_placeholder(value: Optional[str]) -> bool:
    """값이 비었거나 템플릿 플레이스홀더로 보이면 True."""
    if value is None:
        return True
    s = str(value).strip()
    if not s:
        return True
    low = s.lower()
    return any(tok in low for tok in _PLACEHOLDER_SUBSTRINGS)


def is_real_secret(value: Optional[str]) -> bool:
    """진짜 비밀값으로 보이면 True(= 비어있지 않고 플레이스홀더 아님)."""
    return not looks_like_placeholder(value)


def resolve_secret(candidates: Iterable[Optional[str]]) -> Optional[str]:
    """후보들을 우선순위 순서로 받아 '진짜처럼 보이는' 첫 값을 반환. 없으면 None.

    호출자는 우선순위대로 candidates 를 구성한다(예: .env 스냅샷 → st.secrets → os.environ).
    플레이스홀더·빈 값은 건너뛴다 — 진짜 키가 플레이스홀더에 덮이지 않게 보장.
    """
    for c in candidates:
        if is_real_secret(c):
            return str(c).strip()
    return None
