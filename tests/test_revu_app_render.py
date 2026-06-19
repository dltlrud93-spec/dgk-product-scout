"""
test_revu_app_render.py — 체험단 양식 화면의 ★실제 렌더 경로 회귀 테스트(AppTest).

배경: 단위 테스트(test_revu_form.py)는 build_revu_docx 를 명시 인자로 직접 부르고,
headless 부팅은 비밀번호 게이트에서 멈춰 render_revu_form 이 실행되지 않는다. 그래서
"app.py 가 RevuFormData(...) 에 넘기는 인자"와 "RevuFormData 필드"의 불일치가
단위 테스트·부팅으로는 안 잡히고 배포본에서 TypeError 로 터졌다(line 1659).

이 테스트는 streamlit AppTest 로 게이트를 통과시키고 '체험단 양식' 화면을 실제로
렌더해, RevuFormData 생성 → build_revu_docx 까지 실제 경로가 예외 없이 도는지 본다.
app↔dataclass 인자 불일치가 생기면 at.exception 으로 잡혀 이 테스트가 실패한다.
"""

from __future__ import annotations

import pytest

from src.revu_form import TEMPLATE_PATH

AppTest = pytest.importorskip(
    "streamlit.testing.v1", reason="streamlit AppTest 미지원 버전"
).AppTest

pytestmark = pytest.mark.skipif(
    not TEMPLATE_PATH.exists(),
    reason="빈 양식 템플릿(templates/revu_basic_template.docx)이 없으면 건너뜀",
)

_APP = str(TEMPLATE_PATH.parent.parent / "app.py")


def _open_revu_form():
    """게이트 통과 + '체험단 양식' 화면 직행(네트워크 무거운 기본화면 회피) 후 렌더."""
    at = AppTest.from_file(_APP, default_timeout=60)
    at.session_state["_authenticated"] = True
    at.session_state["_screen_select"] = "체험단 양식"
    at.run()
    return at


def test_render_revu_form_no_exception():
    """★실제 render_revu_form 경로가 예외 없이 돈다 — RevuFormData 인자 불일치 회귀 방지."""
    at = _open_revu_form()
    assert not at.exception, f"render_revu_form 에서 예외: {at.exception}"


def test_render_revu_form_has_option_radios():
    """Step1 옵션 라디오 3개(콘텐츠 타입·구매평 결합·긴급 진행)가 렌더된다."""
    at = _open_revu_form()
    labels = {r.label for r in at.radio}
    assert {"콘텐츠 타입", "구매평 결합", "긴급 진행"} <= labels


def test_render_revu_form_builds_docx_download():
    """RevuFormData 생성 → build_revu_docx 성공 → 다운로드 버튼이 존재한다.

    (build 가 예외였다면 try/except 가 st.error 를 띄우고 버튼이 없다 → 실패.)"""
    at = _open_revu_form()
    assert len(at.get("download_button")) >= 1


def test_render_revu_form_toggle_options_no_exception():
    """옵션 라디오를 바꿔 RevuFormData 재생성 경로를 다시 타도 예외가 없어야 한다."""
    at = _open_revu_form()
    for r in at.radio:
        if r.label == "콘텐츠 타입":
            r.set_value("클립")
        if r.label == "구매평 결합":
            r.set_value("예")
        if r.label == "긴급 진행":
            r.set_value("예")
    at.run()
    assert not at.exception, f"옵션 변경 후 예외: {at.exception}"
    assert len(at.get("download_button")) >= 1
