"""
test_keyword_ai.py — AI 키워드 생성(Claude) 파싱·폴백·키처리. ★실제 API 호출 금지(mock).

client 주입으로 anthropic SDK 없이도 전 경로 검증. 파싱(JSON/펜스/줄폴백)·빈결과·
에러흡수·키없음 예외·프롬프트(구매형 사전·차종/제품 주입)를 확인한다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.keyword_ai import (
    AiKeyMissingError,
    generate_ai_keywords,
    parse_ai_keywords,
    resolve_api_key,
)


# ── 가짜 anthropic 클라이언트 ───────────────────────────────────────────────
class _FakeClient:
    """messages.create(**kw) → content[0].text. raise_exc 주면 예외."""

    def __init__(self, text=None, raise_exc=None):
        self._text = text
        self._raise = raise_exc
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if self._raise is not None:
            raise self._raise
        return SimpleNamespace(content=[SimpleNamespace(text=self._text)])


# ── parse_ai_keywords: JSON / 펜스 / 줄폴백 / 빈결과 / 정규화 ────────────────
def test_parse_plain_json_array():
    assert parse_ai_keywords('["활성탄","캐빈필터","냄새"]') == ["활성탄", "캐빈필터", "냄새"]


def test_parse_strips_json_code_fence():
    text = '```json\n["EV5에어컨필터냄새","활성탄"]\n```'
    assert parse_ai_keywords(text) == ["EV5에어컨필터냄새", "활성탄"]


def test_parse_strips_plain_fence():
    assert parse_ai_keywords('```\n["발수","사이즈"]\n```') == ["발수", "사이즈"]


def test_parse_line_fallback_when_not_json():
    text = "활성탄\n캐빈필터\n- 냄새\n1. 교체비용"
    assert parse_ai_keywords(text) == ["활성탄", "캐빈필터", "냄새", "교체비용"]


def test_parse_empty_returns_empty():
    assert parse_ai_keywords("") == []
    assert parse_ai_keywords("   ") == []


def test_parse_non_list_json_falls_back():
    # 객체(JSON 이지만 배열 아님) → 줄폴백으로라도 토큰화(빈 줄만 아니면)
    assert parse_ai_keywords('{"a":1}') == ['{"a":1}'.replace(" ", "")]


def test_parse_normalizes_spaces_and_dedupes():
    assert parse_ai_keywords('["에어컨 필터","에어컨필터","  활성탄  ",""]') == [
        "에어컨필터", "활성탄"]


# ── generate_ai_keywords: client 주입(실호출 없음) ──────────────────────────
def test_generate_with_injected_client_parses():
    client = _FakeClient(text='["활성탄","캐빈필터교체","냄새"]')
    out = generate_ai_keywords("EV5", "에어컨필터", client=client)
    assert out == ["활성탄", "캐빈필터교체", "냄새"]


def test_generate_injects_buy_dict_and_target_into_prompt():
    client = _FakeClient(text='[]')
    generate_ai_keywords("EV5", "에어컨필터", client=client)
    kw = client.calls[0]
    # 🟢구매형 사전(단일 출처)이 system 에 주입됐는지
    assert "추천" in kw["system"] and "활성탄" in kw["system"]
    # 차종·제품이 user 프롬프트에 들어갔는지
    user = kw["messages"][0]["content"]
    assert "EV5" in user and "에어컨필터" in user
    assert "20~25" in user   # 개수 요구


def test_generate_api_error_returns_empty():
    client = _FakeClient(raise_exc=RuntimeError("429 Too Many Requests"))
    assert generate_ai_keywords("EV5", "에어컨필터", client=client) == []


def test_generate_empty_product_returns_empty_without_call():
    client = _FakeClient(text='["x"]')
    assert generate_ai_keywords("EV5", "", client=client) == []
    assert client.calls == []   # 제품 없으면 API 호출 자체를 안 함


def test_generate_wiper_product():
    client = _FakeClient(text='["와이퍼사이즈","발수","교체주기"]')
    out = generate_ai_keywords("쏘렌토", "와이퍼", client=client)
    assert out == ["와이퍼사이즈", "발수", "교체주기"]


# ── 키 처리 ────────────────────────────────────────────────────────────────
def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AiKeyMissingError):
        generate_ai_keywords("EV5", "에어컨필터")   # client 없음 → 키 필요


def test_resolve_api_key_prefers_explicit_then_env(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert resolve_api_key("sk-real-key") == "sk-real-key"
    assert resolve_api_key(None) is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
    assert resolve_api_key(None) == "sk-from-env"


def test_resolve_api_key_skips_placeholder(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # 플레이스홀더는 건너뛴다(secrets_util 재사용)
    assert resolve_api_key("your_key_here") is None
