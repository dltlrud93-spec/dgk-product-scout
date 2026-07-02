"""
test_vault_app_render.py — 황금 발굴함 화면의 실제 렌더 경로 스모크(AppTest).

네트워크 0: 시트/인증 경계 함수를 monkeypatch 로 대체(발굴함=주입 FakeWS 또는 None,
집행 이력=빈 집합, 데이터 차종 읽기=인증 실패 폴백). at.exception 없음 + '조견표' 미노출 검증.
"""

from __future__ import annotations

import pathlib

import pytest

import streamlit as st

from src.core.vault import VAULT_HEADER, make_vault_row

AppTest = pytest.importorskip(
    "streamlit.testing.v1", reason="streamlit AppTest 미지원 버전"
).AppTest

_APP = str(pathlib.Path(__file__).resolve().parent.parent / "app.py")


class _FakeWS:
    def __init__(self, rows):
        self._rows = [list(r) for r in rows]

    def get_all_values(self):
        return [list(r) for r in self._rows]


def _raise(*_a, **_k):
    raise RuntimeError("인증 없음(테스트 폴백)")


def _isolate_network(monkeypatch, vault_ws):
    """모든 시트/인증 경계를 대체 — 네트워크 0 보장."""
    monkeypatch.setattr("src.core.vault.open_vault_worksheet", lambda **k: vault_ws)
    # 집행 이력(URL 로그) 미설정 → 빈 집합
    monkeypatch.setattr("src.core.url_log._resolve_log_sheet_id", lambda *a, **k: "")
    # 데이터 차종 읽기(조견표) 인증 실패 → render_vault 가 [] 폴백
    monkeypatch.setattr("src.core.jogyeonpyo._authorize", _raise)
    st.cache_data.clear()   # 새 화면 캐시(_read_vault_cached 등) 무효화


def _open_vault(monkeypatch, vault_ws):
    _isolate_network(monkeypatch, vault_ws)
    at = AppTest.from_file(_APP, default_timeout=60)
    at.session_state["_authenticated"] = True
    at.session_state["_screen_select"] = "황금 발굴함"
    at.run()
    return at


def _all_text(at) -> str:
    parts = []
    for attr in ("title", "header", "subheader", "markdown", "caption",
                 "text", "info", "success", "warning", "error"):
        try:
            for el in getattr(at, attr):
                parts.append(getattr(el, "value", "") or "")
        except Exception:  # noqa: BLE001 — 일부 접근자 미지원 버전 대비
            pass
    return " ".join(parts)


def test_render_vault_empty_smoke(monkeypatch):
    """발굴함이 비어도(open→None) 화면이 예외 없이 렌더된다."""
    at = _open_vault(monkeypatch, None)   # open_vault_worksheet → None
    assert not at.exception, f"render_vault 예외: {at.exception}"
    assert "황금 발굴함" in _all_text(at)


def test_render_vault_populated_smoke(monkeypatch):
    """발굴함에 정상/포화/잠복 행이 있을 때 표·칩·그룹이 예외 없이 렌더된다."""
    rows = [
        VAULT_HEADER,
        make_vault_row("2026-02-01 09:00", "에어컨필터", "셀토스", "셀토스에어컨필터", 1000,
                       doc_count=50, ratio=0.05, grade="🟡 황금", recent_3m=3,
                       opportunity_score=952, status="정상"),
        make_vault_row("2026-02-01 09:00", "에어컨필터", "쏘렌토", "쏘렌토에어컨필터", 2000,
                       doc_count=8000, ratio=4.0, grade="🔴 포화/후순위", recent_3m=120,
                       opportunity_score=222, status="정상"),
        make_vault_row("2026-02-01 09:00", "와이퍼", "신차", "신차와이퍼", 5, status="잠복"),
    ]
    at = _open_vault(monkeypatch, _FakeWS(rows))
    assert not at.exception, f"render_vault 예외: {at.exception}"
    txt = _all_text(at)
    assert "1순위" in txt and "잠복" in txt


def test_render_vault_no_jogyeonpyo_wording(monkeypatch):
    """발굴함 화면 사용자 노출 문자열에 '조견표'가 없다(용어 규약 — 전부 '데이터')."""
    rows = [
        VAULT_HEADER,
        make_vault_row("2026-02-01 09:00", "에어컨필터", "셀토스", "셀토스에어컨필터", 1000,
                       doc_count=50, ratio=0.05, grade="🟡 황금", recent_3m=3,
                       opportunity_score=952, status="정상"),
    ]
    at = _open_vault(monkeypatch, _FakeWS(rows))
    assert not at.exception
    assert "조견표" not in _all_text(at)
