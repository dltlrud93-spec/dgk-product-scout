"""
keyword_ai.py — Claude API 로 (차종+제품) 기반 ★구매형 키워드를 생성하는 순수 함수.

streamlit 의존 없음(테스트·재사용 가능). API 키는 resolve_secret 로만 해석한다.
🟢구매형 단어 사전은 keyword_intent.BUY_KEYWORDS 를 ★import 해 동적 주입(중복 정의 금지).

설계:
  · anthropic SDK 는 ★지연 import(미설치 배포본에서도 import 시점에 안 죽게 — 메모리: 선택적 의존성).
  · 키 없으면 명확한 예외(AiKeyMissingError). API/파싱 에러는 [] 반환(앱이 안 죽게).
  · 모델·토큰은 config 상수(CLAUDE_MODEL/CLAUDE_MAX_TOKENS) — 숫자 코드 박지 않기(스펙 12행).
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

from config import CLAUDE_MAX_TOKENS, CLAUDE_MODEL
from src.core.keyword_intent import BUY_KEYWORDS
from src.core.secrets_util import resolve_secret

_SECRET_KEY = "ANTHROPIC_API_KEY"

# 차량 일반어(키워드에서 빼라고 명시할 노이즈) — 블로그 필터의 의도와 일관.
_VEHICLE_NOISE_HINT = "보조금·연비·유지비·풀체인지·제원·견적·트림·출고·주행거리"


class AiKeyMissingError(RuntimeError):
    """ANTHROPIC_API_KEY 가 없을 때(앱은 버튼 비활성화로 미리 막지만 안전망)."""


def resolve_api_key(explicit: Optional[str] = None) -> Optional[str]:
    """API 키를 해석한다(explicit → os.environ 순). 없으면 None.

    ★순수 함수 유지를 위해 st.secrets 는 직접 안 본다 — Cloud 에선 app 이
    st.secrets 로 해석한 키를 explicit 로 넘긴다(generate_ai_keywords(api_key=...))."""
    return resolve_secret([explicit, os.environ.get(_SECRET_KEY)])


def _build_system_prompt() -> str:
    """🟢구매형 단어 사전을 주입한 system 프롬프트(단일 출처 재사용)."""
    buy_words = ", ".join(BUY_KEYWORDS)
    return (
        "당신은 한국 자동차 소모품(에어컨필터·와이퍼) 체험단 마케팅 키워드 전문가입니다.\n"
        "목표: 아직 제품을 사지 않았거나 불편을 해결하려는 사람이 검색할 ★구매형 키워드를 만든다.\n"
        "아래는 구매형 키워드의 성격을 보여주는 예시 사전입니다. 이 성격(추천/스펙/불편/비용 등)으로 생성하세요:\n"
        f"{buy_words}\n"
        "이미 산 사람이 찾는 정보형(교체방법·셀프교체·청소·장착 등)은 ★만들지 마세요."
    )


def _build_user_prompt(vehicle: str, product: str) -> str:
    """차종+제품 요구사항 user 프롬프트."""
    v = (vehicle or "").strip()
    p = (product or "").strip()
    target = f"차종={v or '(차종 없음)'}, 제품={p}"
    return (
        f"{target} 용 구매형 키워드를 20~25개 생성하세요.\n"
        "요구사항:\n"
        "- 한국어, 각 키워드는 ★공백 없는 단일 토큰.\n"
        "- (차종+제품+의도) 롱테일과 단독 의도어를 ★혼합. "
        f"예: {(v or '차종')}{p}냄새 / 활성탄 / {p}교체.\n"
        f"- 차량 일반어 제외: {_VEHICLE_NOISE_HINT} 등.\n"
        "- 응답은 ★JSON 문자열 배열로만. 설명·마크다운·코드펜스 금지.\n"
        '예: ["키워드1","키워드2"]'
    )


# ```json … ``` 코드펜스(언어 태그 유무 무관) 제거용.
_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$")


def parse_ai_keywords(text: str) -> list[str]:
    """모델 응답 텍스트 → 키워드 리스트. JSON 우선, 실패 시 줄단위 폴백.

    빈/파싱불가/형식이상은 [] 반환(앱이 안 죽게). 공백 제거·중복 제거·빈 토큰 제외.
    """
    if not text or not text.strip():
        return []
    cleaned = _FENCE_RE.sub("", text.strip()).strip()

    items: list = []
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            items = parsed
        else:
            raise ValueError("배열 아님")
    except Exception:  # noqa: BLE001 — JSON 실패 시 줄단위 폴백
        items = [
            re.sub(r'^[\s"\'\-\*\d\.\)\]]+|[\s"\',]+$', "", ln)
            for ln in cleaned.splitlines()
        ]

    out: list[str] = []
    seen: set[str] = set()
    for it in items:
        kw = re.sub(r"\s+", "", str(it)).strip()
        if not kw or kw in seen:
            continue
        seen.add(kw)
        out.append(kw)
    return out


def generate_ai_keywords(
    vehicle: str,
    product: str,
    *,
    api_key: Optional[str] = None,
    client=None,
    model: str = CLAUDE_MODEL,
    max_tokens: int = CLAUDE_MAX_TOKENS,
) -> list[str]:
    """차종+제품으로 구매형 키워드를 생성한다. 실패·빈결과는 [] (앱 안 죽음).

    product 가 비면 [](제품은 필수). client 를 주입하면 그걸 쓰고(테스트), 없으면
    api_key(또는 환경)로 anthropic 클라이언트를 ★지연 생성한다. 키가 전혀 없으면
    AiKeyMissingError 를 던진다(앱은 버튼 비활성화로 미리 막음).
    """
    if not (product or "").strip():
        return []

    if client is None:
        key = resolve_api_key(api_key)
        if not key:
            raise AiKeyMissingError(
                f"{_SECRET_KEY} 가 없습니다 — Streamlit Secrets 에 추가하세요."
            )
        import anthropic  # ★지연 import(선택적 의존성)

        client = anthropic.Anthropic(api_key=key)

    try:
        msg = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_build_system_prompt(),
            messages=[{"role": "user", "content": _build_user_prompt(vehicle, product)}],
        )
        text = msg.content[0].text
        return parse_ai_keywords(text)
    except Exception:  # noqa: BLE001 — API/네트워크/형식 오류는 빈 결과로(앱 보호)
        return []
