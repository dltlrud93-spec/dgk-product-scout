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


def test_render_revu_form_has_four_tabs():
    """7개 섹션이 st.tabs 4개로 그룹핑돼 렌더되고, 탭 밖 RevuFormData 조립까지 도달한다.

    탭은 매 실행마다 4개 모두 렌더 → 모든 위젯 인스턴스화 → 탭 밖 docx 다운로드 위젯이
    정상 생성된다(조립 실패 시 download_button 이 사라져 실패)."""
    at = _open_revu_form()
    assert not at.exception, f"탭 렌더 예외: {at.exception}"
    assert len(at.tabs) == 4, f"탭이 4개가 아님: {len(at.tabs)}"
    assert len(at.get("download_button")) >= 1   # RevuFormData→build_revu_docx 도달


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


# ── 키워드 추천(검색광고 연관어 + 블로그 제목) 실제 렌더 경로 ───────────────────

def test_render_keyword_reco_sections_present():
    """🔍 키워드 추천 패널에 검색광고·블로그 두 섹션 라벨이 렌더된다(무예외)."""
    at = _open_revu_form()
    assert not at.exception
    blob = " ".join(m.value for m in at.markdown)
    assert "검색광고 연관키워드" in blob
    assert "블로그 제목 기반 키워드" in blob


def test_render_blog_reco_results_no_exception():
    """블로그 추천 결과(키워드+원문 제목)를 주입해 렌더 — 체크박스·제목 expander 무예외."""
    at = AppTest.from_file(_APP, default_timeout=60)
    at.session_state["_authenticated"] = True
    at.session_state["_screen_select"] = "체험단 양식"
    at.session_state["revu_blog_reco"] = {
        "keywords": [("교체", 5), ("캐빈필터", 3), ("냄새", 2)],
        "titles": ["EV5 에어컨필터 교체 후기", "EV5 캐빈필터 교체주기"],
        "excluded": [],
    }
    at.run()
    assert not at.exception, f"블로그 추천 결과 렌더 예외: {at.exception}"
    cks = {c.label for c in at.checkbox}
    assert any("교체" in lbl for lbl in cks)


def test_render_blog_reco_failure_only_warns_no_exception():
    """블로그 추천 실패(error) 주입 — 경고만 뜨고 화면·양식은 그대로(무예외)."""
    at = AppTest.from_file(_APP, default_timeout=60)
    at.session_state["_authenticated"] = True
    at.session_state["_screen_select"] = "체험단 양식"
    at.session_state["revu_blog_reco"] = {"error": "RuntimeError: 429 Too Many Requests"}
    at.run()
    assert not at.exception, f"블로그 실패 렌더 예외: {at.exception}"
    # 블로그 실패해도 docx 다운로드 버튼은 그대로(양식 동작 유지)
    assert len(at.get("download_button")) >= 1


# ── 구매 의도 분류(뱃지·정렬·정보형 접힘) 실제 렌더 경로 ─────────────────────

def test_render_reco_intent_badges_and_info_collapsed():
    """검색광고 연관어에 🟢/🟡/🔴 뱃지가 붙고, 정보형은 접힘 + 안내문이 렌더된다."""
    at = AppTest.from_file(_APP, default_timeout=60)
    at.session_state["_authenticated"] = True
    at.session_state["_screen_select"] = "체험단 양식"
    at.session_state["revu_reco"] = {
        "clean": [
            ("에어컨필터추천", 100),       # 🟢 buy
            ("에어컨필터교체주기", 50),    # 🟡 mid
            ("에어컨필터교체방법", 30),    # 🔴 info
        ],
        "excluded": [],
    }
    at.run()
    assert not at.exception, f"분류 렌더 예외: {at.exception}"
    cks = {c.label for c in at.checkbox}
    assert any(lbl.startswith("🟢") and "추천" in lbl for lbl in cks)
    assert any(lbl.startswith("🟡") and "교체주기" in lbl for lbl in cks)
    assert any(lbl.startswith("🔴") and "교체방법" in lbl for lbl in cks)
    # 정보형은 접힌 expander 로 분리(라벨에 안내문) — 완전히 숨기지 않음
    exp_labels = [e.label for e in at.expander]
    assert any("이미 구매한 사람이 찾는 키워드" in lbl for lbl in exp_labels)


def test_render_blog_reco_intent_badges():
    """블로그 제목 키워드에도 동일하게 분류 뱃지가 적용된다."""
    at = AppTest.from_file(_APP, default_timeout=60)
    at.session_state["_authenticated"] = True
    at.session_state["_screen_select"] = "체험단 양식"
    at.session_state["revu_blog_reco"] = {
        "keywords": [("냄새", 5), ("교체주기", 3), ("셀프교체", 2)],
        "titles": ["EV5 에어컨필터 냄새", "EV5 셀프교체"],
        "excluded": [],
    }
    at.run()
    assert not at.exception, f"블로그 분류 렌더 예외: {at.exception}"
    cks = {c.label for c in at.checkbox}
    assert any(lbl.startswith("🟢") and "냄새" in lbl for lbl in cks)   # buy
    assert any(lbl.startswith("🟡") and "교체주기" in lbl for lbl in cks)  # mid
    assert any(lbl.startswith("🔴") and "셀프교체" in lbl for lbl in cks)  # info(접힘)


def test_render_blog_reco_product_filter_note():
    """제품 관련성 필터 안내(제품 관련 제목 N/M개)가 렌더된다(무예외)."""
    at = AppTest.from_file(_APP, default_timeout=60)
    at.session_state["_authenticated"] = True
    at.session_state["_screen_select"] = "체험단 양식"
    at.session_state["revu_blog_reco"] = {
        "keywords": [("교체주기", 1), ("활성탄", 1)],
        "titles": ["EV5 에어컨필터 교체주기 활성탄", "EV5 보조금 연비 총정리"],
        "excluded": [],
        "n_product_titles": 1,
        "n_total_titles": 2,
    }
    at.run()
    assert not at.exception, f"제품 필터 안내 렌더 예외: {at.exception}"
    caps = " ".join(c.value for c in at.caption)
    assert "제품 관련 제목" in caps


def test_render_blog_reco_no_product_titles_warns():
    """제품 관련 제목 0개면 경고만 뜨고 화면은 그대로(무예외)."""
    at = AppTest.from_file(_APP, default_timeout=60)
    at.session_state["_authenticated"] = True
    at.session_state["_screen_select"] = "체험단 양식"
    at.session_state["revu_blog_reco"] = {
        "keywords": [],
        "titles": ["EV5 보조금 연비", "EV5 풀체인지 주행거리"],
        "excluded": [],
        "n_product_titles": 0,
        "n_total_titles": 2,
    }
    at.run()
    assert not at.exception, f"제품 0개 경고 렌더 예외: {at.exception}"
    warns = " ".join(w.value for w in at.warning)
    assert "제품 관련 블로그 글을 찾지 못했습니다" in warns
    assert len(at.get("download_button")) >= 1   # 양식은 그대로 동작


# ── 🤖 AI 키워드 자동완성 실제 렌더 경로 ─────────────────────────────────────

def test_render_ai_section_present_and_button():
    """AI 자동완성 섹션 라벨 + 생성 버튼이 렌더된다(키 없으면 안내 캡션)."""
    at = _open_revu_form()
    assert not at.exception
    blob = " ".join(m.value for m in at.markdown)
    assert "AI 키워드 자동완성" in blob
    btns = {b.label for b in at.button}
    assert "AI로 키워드 생성" in btns
    # 로컬/테스트엔 ANTHROPIC_API_KEY 가 없어 안내 캡션이 뜬다
    caps = " ".join(c.value for c in at.caption)
    assert "ANTHROPIC_API_KEY" in caps


def test_render_ai_reco_results_badges():
    """주입된 AI 키워드가 🟢/🟡/🔴 분류 뱃지로 렌더되고 정보형은 접힌다."""
    at = AppTest.from_file(_APP, default_timeout=60)
    at.session_state["_authenticated"] = True
    at.session_state["_screen_select"] = "체험단 양식"
    at.session_state["revu_ai_reco"] = {
        "clean": [
            ("EV5에어컨필터냄새", None),   # 🟢 buy
            ("에어컨필터교체주기", None),  # 🟡 mid
            ("에어컨필터교체방법", None),  # 🔴 info
        ],
        "excluded": [],
    }
    at.run()
    assert not at.exception, f"AI 키워드 렌더 예외: {at.exception}"
    cks = {c.label for c in at.checkbox}
    assert any(lbl.startswith("🟢") and "냄새" in lbl for lbl in cks)
    assert any(lbl.startswith("🟡") and "교체주기" in lbl for lbl in cks)
    assert any(lbl.startswith("🔴") and "교체방법" in lbl for lbl in cks)
    # 추가 버튼(제목/본문)도 존재
    btns = {b.label for b in at.button}
    assert "➕ 제목키워드에 추가" in btns and "➕ 본문키워드에 추가" in btns


# ── 미션 각도 선택 + 채우기 실제 렌더 경로 ───────────────────────────────────

def test_render_mission_angle_select_and_fill():
    """미션 각도 셀렉트박스+버튼 렌더, 채우면 미션 필드가 리치하게 채워진다(🔴 없음)."""
    at = AppTest.from_file(_APP, default_timeout=60)
    at.session_state["_authenticated"] = True
    at.session_state["_screen_select"] = "체험단 양식"
    at.session_state["revu_car"] = "EV5"
    at.session_state["revu_product"] = "에어컨필터"
    at.run()
    assert not at.exception, f"미션 UI 렌더 예외: {at.exception}"
    assert "미션 각도 선택" in {s.label for s in at.selectbox}

    # ✨ 채우기 버튼 클릭 → 미션 채워짐
    clicked = False
    for b in at.button:
        if b.label.startswith("✨"):
            b.click()
            clicked = True
    assert clicked, "미션 채우기 버튼을 찾지 못함"
    at.run()
    assert not at.exception, f"미션 채움 후 예외: {at.exception}"

    m = [at.session_state[f"revu_mission_{i}"] for i in range(3)]
    assert all(line.strip() for line in m), "미션이 비어있음"
    assert "EV5" in m[0] and "에어컨필터" in m[0]
    blob = "".join(m).replace(" ", "")
    assert "교체방법" not in blob and "셀프" not in blob   # 🔴정보형 없음


def test_render_mission_fields_are_text_areas():
    """미션 입력칸이 다줄 편집 가능한 text_area 로 렌더된다(리치 미션 표시·편집)."""
    at = AppTest.from_file(_APP, default_timeout=60)
    at.session_state["_authenticated"] = True
    at.session_state["_screen_select"] = "체험단 양식"
    at.session_state["revu_car"] = "EV5"
    at.session_state["revu_product"] = "와이퍼"
    at.run()
    assert not at.exception
    ta_labels = {t.label for t in at.text_area}
    assert {"미션 1", "미션 2", "미션 3"} <= ta_labels


# ── nt_ 새 규칙 UI(날짜·자동조립·상품번호 추출) 렌더 ─────────────────────────

def test_render_nt_generator_new_controls():
    """모집요청일 date_input + '자동 조립'·'상품번호 추출' 버튼이 렌더된다(무예외)."""
    at = _open_revu_form()
    assert not at.exception
    btns = {b.label for b in at.button}
    assert any("자동 조립" in b for b in btns)
    assert any("상품번호 추출" in b for b in btns)
    assert len(at.date_input) >= 1   # 모집 요청일


def test_render_url_log_buttons_when_url_generated():
    """track_url 이 생성되면 이력 저장/보기 버튼이 노출된다(무예외)."""
    at = AppTest.from_file(_APP, default_timeout=60)
    at.session_state["_authenticated"] = True
    at.session_state["_screen_select"] = "체험단 양식"
    at.session_state["revu_track_base"] = "https://m.site.naver.com/2aAvQ"
    at.session_state["revu_nt_source"] = "naver.blog"
    at.session_state["revu_nt_medium"] = "N_REVU"
    at.run()
    assert not at.exception, f"URL 이력 버튼 렌더 예외: {at.exception}"
    btns = {b.label for b in at.button}
    assert any("이력 저장" in b for b in btns)
    assert any("이력 보기" in b for b in btns)


# ── 체험단 성과 분석 화면 렌더 ───────────────────────────────────────────────

def test_render_campaign_analytics_empty():
    """분석 화면 진입 시 안내문이 뜨고 예외가 없다."""
    at = AppTest.from_file(_APP, default_timeout=60)
    at.session_state["_authenticated"] = True
    at.session_state["_screen_select"] = "체험단 성과 분석"
    at.run()
    assert not at.exception, f"분석 화면 렌더 예외: {at.exception}"
    blob = " ".join(m.value for m in at.markdown)
    assert "체험단 성과 분석" in (blob + " " + " ".join(t.value for t in at.title))


def test_render_campaign_analytics_with_result():
    """집계 결과 주입 시 요약·매체·캠페인·제품 블록이 무예외로 렌더된다."""
    at = AppTest.from_file(_APP, default_timeout=60)
    at.session_state["_authenticated"] = True
    at.session_state["_screen_select"] = "체험단 성과 분석"
    at.session_state["ca_result"] = {
        "summary": {"inflow": 1585, "pay": 113, "pay_rate": 7.13, "amount": 2919930},
        "by_medium": [
            {"medium": "blog", "inflow": 305, "pay": 28, "rate": 9.18, "amount": 1000000},
            {"medium": "revu", "inflow": 286, "pay": 14, "rate": 4.9, "amount": 500000},
        ],
        "by_campaign": [
            {"campaign": "P01filter2ea", "inflow": 229, "pay": 9, "rate": 3.9,
             "amount": 180000, "tag": "bad"},
        ],
        "by_product": [
            {"product": "와이퍼", "inflow": 76, "pay": 19, "rate": 25.0, "amount": 300000},
        ],
        "warnings": ["nt_medium 대소문자 혼용(예: REVU/revu) — 분리 집계됨"],
    }
    at.run()
    assert not at.exception, f"분석 결과 렌더 예외: {at.exception}"
    blob = " ".join(m.value for m in at.markdown)
    assert "P01filter2ea" in blob and "와이퍼" in blob
    warns = " ".join(w.value for w in at.warning)
    assert "대소문자" in warns


def _analytics_html(**result_extra):
    """성과분석 화면을 렌더하고 표 HTML(markdown blob)을 돌려준다."""
    at = AppTest.from_file(_APP, default_timeout=60)
    at.session_state["_authenticated"] = True
    at.session_state["_screen_select"] = "체험단 성과 분석"
    base = {
        "summary": {"inflow": 0, "pay": 0, "pay_rate": 0, "amount": 0, "amount14": 0},
        "by_medium": [], "by_campaign": [], "by_product": [], "warnings": [],
    }
    base.update(result_extra)
    at.session_state["ca_result"] = base
    at.run()
    assert not at.exception, f"성과표 렌더 예외: {at.exception}"
    return at, " ".join(m.value for m in at.markdown)


def test_perf_table_html_badges_colors_and_bar_width():
    """_perf_table_html: 뱃지(★/⚠)·결제율 색·막대 width(rate/15*100, 100 클램프)·콤마."""
    at, blob = _analytics_html(
        by_medium=[
            # rate=15 → green #16a34a, width 100% ; rate=7.5 → mid #334155, width 50%
            {"medium": "blog", "inflow": 1234, "pay": 185, "rate": 15.0,
             "amount": 1000000, "pay14": 200, "amount14": 1100000},
            {"medium": "revu", "inflow": 100, "pay": 7, "rate": 7.5,
             "amount": 50000, "pay14": 8, "amount14": 55000},
            # rate=30 → green, width 클램프 100% (15 초과여도 100 넘지 않음)
            {"medium": "cafe", "inflow": 50, "pay": 15, "rate": 30.0,
             "amount": 30000, "pay14": 16, "amount14": 33000},
        ],
        by_campaign=[
            {"campaign": "good_camp", "inflow": 10, "pay": 3, "rate": 30.0,
             "amount": 9000, "tag": "good"},
            {"campaign": "bad_camp", "inflow": 10, "pay": 0, "rate": 2.0,
             "amount": 0, "tag": "bad"},
        ],
    )
    # ① good→★ 초록 / bad→⚠ 빨강 (뱃지 색)
    assert "★" in blob and "#16a34a" in blob
    assert "⚠" in blob and "#dc2626" in blob
    # ② 결제율 색: 15%↑ 초록 / 5~15 중간(#334155) / 5%미만 빨강
    assert "#334155" in blob          # rate 7.5 (중간)
    # ③ 막대 width 정확: 15→100, 7.5→50, 30→100(클램프)
    assert "width:100%" in blob and "width:50%" in blob
    # ④ +14열 헤더 + pay14/amount14 콤마 포맷
    assert "+14결제" in blob and "+14금액" in blob
    assert "1,100,000" in blob        # amount14 콤마
    # ⑤ 유입/결제 콤마 포맷
    assert "1,234" in blob


def test_perf_table_html_escapes_names_and_empty():
    """이름 html.escape 적용 + 빈 리스트 섹션은 표가 없다(캡션만)."""
    at, blob = _analytics_html(
        by_medium=[{"medium": "a & b", "inflow": 1, "pay": 0, "rate": 1.0,
                    "amount": 0, "pay14": 0, "amount14": 0}],
    )
    assert "a &amp; b" in blob        # & → &amp; (XSS·깨짐 방지)
    # by_campaign 비어있음 → 캠페인 표 markdown 미출력(빈 문자열은 렌더 안 함)
    assert "cap-tbl" in blob          # 매체 표는 존재
    # 빈 by_medium 이면 안내 캡션
    at2, blob2 = _analytics_html(by_medium=[])
    caps = " ".join(c.value for c in at2.caption)
    assert "매체 데이터가 없습니다" in caps


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


# ── 체험단 타겟 순위 그룹 화면 렌더 ──────────────────────────────────────────

def _teamp_rows_fixture():
    """세 등급이 모두 있는 TeampKwRow 리스트(순위 그룹 3개가 다 뜨도록)."""
    from src.core.teamp_mode import TeampKwRow, classify_ratio

    def _row(kw, cm, vol, docs):
        ratio = docs / vol
        return TeampKwRow(keyword=kw, car_model=cm, volume=vol,
                          doc_count=docs, ratio=ratio, grade=classify_ratio(ratio))

    return [
        _row("셀토스에어컨필터", "셀토스KX3", 5000, 500),    # 0.10 → 🟡 황금
        _row("아반떼에어컨필터", "아반떼", 2000, 3000),       # 1.50 → 🟢 해볼만
        _row("모닝에어컨필터", "모닝", 3260, 18850),          # 5.78 → 🔴 포화
    ]


def _open_teamp_with_rows():
    """체험단 타겟 화면을 캐시된 결과 주입으로 렌더(네트워크 없이 순위 그룹 표시)."""
    at = AppTest.from_file(_APP, default_timeout=60)
    at.session_state["_authenticated"] = True
    at.session_state["_screen_select"] = "체험단 타겟"
    sig = ("키워드로 차종 검색", (), "", 0)   # 빈 입력 복귀 경로가 이 서명 결과를 읽음
    at.session_state["_teamp_results"] = {
        sig: {
            "signature": sig, "products": [],
            "rows": _teamp_rows_fixture(), "failed_items": [], "jp_failed": [],
        }
    }
    at.session_state["_teamp_last_sig"] = sig
    at.run()
    return at


def test_render_teamp_priority_groups_present():
    """순위 그룹 3개 헤더(1순위 황금/2순위 해볼만/3순위 포화)가 렌더되고 예외 없다."""
    at = _open_teamp_with_rows()
    assert not at.exception, f"체험단 타겟 렌더 예외: {at.exception}"
    blob = " ".join(
        [s.value for s in at.subheader]
        + [e.label for e in at.expander]
        + [m.value for m in at.markdown]
        + [c.value for c in at.caption]
    )
    assert "1순위 · 황금" in blob
    assert "2순위 · 해볼만" in blob
    assert "3순위 · 포화" in blob


def test_render_teamp_no_gold_top_box():
    """황금 TOP 박스는 제거됐다 — 화면 어디에도 '황금 TOP' 문구가 없다."""
    at = _open_teamp_with_rows()
    assert not at.exception
    blob = " ".join(
        [s.value for s in at.subheader]
        + [e.label for e in at.expander]
        + [m.value for m in at.markdown]
        + [c.value for c in at.caption]
        + [t.value for t in at.title]
    )
    assert "황금 TOP" not in blob


# ── 스모크: 차종 수요 / 계절 제품 화면 오프라인 렌더(네트워크 0) ─────────────
# 세션 캐시를 빈 결과로 선주입 → _load_* 네트워크 경로를 타지 않고 빈 상태만 렌더.

def test_render_car_demand_no_exception():
    """차종 수요 화면: 빈 결과 캐시 주입으로 네트워크 없이 렌더 — 예외 없음."""
    at = AppTest.from_file(_APP, default_timeout=60)
    at.session_state["_authenticated"] = True
    at.session_state["_screen_select"] = "차종 수요"
    at.session_state["_demand_results"] = ([], {}, "", {})   # (rows, trends, note, members_map)
    at.run()
    assert not at.exception, f"차종 수요 렌더 예외: {at.exception}"


def test_render_seasonal_no_exception():
    """계절 제품 화면: 빈 결과 캐시 주입으로 네트워크 없이 렌더 — 예외 없음."""
    at = AppTest.from_file(_APP, default_timeout=60)
    at.session_state["_authenticated"] = True
    at.session_state["_screen_select"] = "계절 제품"
    at.session_state["_seasonal_results"] = ([], None, "")   # (rows, winter, note)
    at.run()
    assert not at.exception, f"계절 제품 렌더 예외: {at.exception}"


def test_render_teamp_xlsx_download_button_present():
    """순위 그룹 화면에 xlsx 내보내기 다운로드 버튼이 렌더된다(rows 있을 때)."""
    at = _open_teamp_with_rows()
    assert not at.exception
    assert len(at.get("download_button")) >= 1


def test_render_teamp_collected_at_caption():
    """캐시 재사용 렌더면 '수집: {시각} · 캐시' 캡션이 뜬다(collected_at 주입 시)."""
    at = AppTest.from_file(_APP, default_timeout=60)
    at.session_state["_authenticated"] = True
    at.session_state["_screen_select"] = "체험단 타겟"
    sig = ("키워드로 차종 검색", (), "", 0)
    at.session_state["_teamp_results"] = {
        sig: {
            "signature": sig, "products": [],
            "rows": _teamp_rows_fixture(), "failed_items": [], "jp_failed": [],
            "collected_at": "2026-07-02 09:30",
        }
    }
    at.session_state["_teamp_last_sig"] = sig
    at.run()
    assert not at.exception
    caps = " ".join(c.value for c in at.caption)
    assert "2026-07-02 09:30" in caps and "캐시" in caps


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
