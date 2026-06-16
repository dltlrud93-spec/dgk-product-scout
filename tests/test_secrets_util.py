"""test_secrets_util.py — 플레이스홀더 회피 비밀값 해석 회귀 테스트(1단계 함정 방지)."""

from __future__ import annotations

from src.core.secrets_util import (
    is_real_secret,
    looks_like_placeholder,
    resolve_secret,
)


def test_placeholder_detection_template_values():
    assert looks_like_placeholder("your_ad_api_key_here")
    assert looks_like_placeholder("changeme")
    assert looks_like_placeholder("<your key>")
    assert looks_like_placeholder("")
    assert looks_like_placeholder("   ")
    assert looks_like_placeholder(None)


def test_real_values_not_placeholder():
    assert is_real_secret("0100000000747fd5bea2b92cddfb9")
    assert is_real_secret("AQAAAAolr3...realkey")
    assert not looks_like_placeholder("AQAAAAolr3realkey")


def test_resolve_prefers_first_real_skipping_placeholder():
    # .env(진짜) → st.secrets(플레이스홀더) → os.environ(플레이스홀더)
    assert resolve_secret(["realkey123", "your_ad_api_key_here", "your_x_here"]) == "realkey123"


def test_resolve_skips_leading_placeholder():
    # st.secrets 가 먼저지만 플레이스홀더 → .env 진짜값 채택
    assert resolve_secret(["your_ad_api_key_here", "realkey123"]) == "realkey123"


def test_resolve_strips_whitespace():
    assert resolve_secret(["  realkey  "]) == "realkey"


def test_resolve_all_placeholder_returns_none():
    assert resolve_secret(["your_x_here", "", None, "changeme"]) is None


def test_resolve_empty_returns_none():
    assert resolve_secret([]) is None
