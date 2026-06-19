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

from src.revu_form import (
    TEMPLATE_PATH,
    deserialize_form,
    revu_form_defaults,
    serialize_form,
)

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


# ── 저장/불러오기 실제 렌더 경로 ─────────────────────────────────────────────

def test_render_has_save_and_docx_download_buttons():
    """저장(JSON) + docx 두 개의 download_button 이 렌더된다."""
    at = _open_revu_form()
    assert len(at.get("download_button")) >= 2


def _ss_get(at, key, default=None):
    return at.session_state[key] if key in at.session_state else default


def test_save_then_load_roundtrip_via_real_render():
    """★왕복(실제 렌더): 값 입력→저장 JSON→새 세션 불러오기→모든 칸 복원·무예외.

    불러오기 콜백(_load_revu_form_file)과 동일하게 deserialize 결과를 session_state 로
    주입한 뒤 렌더 → StreamlitAPIException(위젯 값 충돌) 없이 값이 위젯에 반영되는지 확인."""
    # 1) 값 입력 후 저장 JSON 생성(앱 저장 버튼과 동일 경로)
    at = _open_revu_form()
    sample = {
        "revu_content_type": "클립", "revu_purchase_combine": "예", "revu_urgent": "예",
        "revu_title": "제목A", "revu_subtitle": "부제B", "revu_car": "EV5",
        "revu_product": "파이널필터", "revu_qty": "P17 2개", "revu_recruit": 15,
        "revu_titlekw": "키워드1", "revu_bodykw": "키워드2, 키워드3", "revu_url": "https://x/p",
        "revu_mission_0": "m1", "revu_mission_1": "m2", "revu_mission_2": "m3",
        "revu_mgr_name": "홍길동",
    }
    for k, v in sample.items():
        at.session_state[k] = v
    at.run()
    assert not at.exception
    cur = {k: _ss_get(at, k, d) for k, d in revu_form_defaults().items()}
    saved = serialize_form(cur)

    # 2) 새 세션 → 불러오기(콜백과 동일하게 session_state 주입) → 렌더
    at2 = _open_revu_form()
    values, warns = deserialize_form(saved)
    assert warns == []
    for k, v in values.items():
        at2.session_state[k] = v
    at2.run()
    assert not at2.exception, f"불러오기 후 렌더 예외: {at2.exception}"

    # 3) 모든 입력칸 값이 복원됐는지(위젯 session_state 기준)
    for k, v in sample.items():
        assert _ss_get(at2, k) == v, f"복원 불일치: {k}"


def test_load_corrupt_values_render_no_exception():
    """손상/허용밖 값이 섞인 JSON 을 불러와도(보정 후) 렌더가 죽지 않는다."""
    bad_json = (
        '{"form_version": 1, "revu_content_type": "엉뚱값", '
        '"revu_recruit": 99999, "revu_product": "와이퍼"}'
    )
    values, warns = deserialize_form(bad_json)   # 보정: content_type→기본, recruit→999
    at = _open_revu_form()
    for k, v in values.items():
        at.session_state[k] = v
    at.run()
    assert not at.exception, f"보정 후 렌더 예외: {at.exception}"
    assert _ss_get(at, "revu_content_type") == "블로그"   # 허용밖 → 기본 복원
    assert _ss_get(at, "revu_recruit") == 999             # 범위 클램프
    assert _ss_get(at, "revu_product") == "와이퍼"        # 정상 필드 복원
