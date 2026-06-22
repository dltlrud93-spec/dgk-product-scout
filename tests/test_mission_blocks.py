"""
test_mission_blocks.py — 실데이터 기반 5단 미션 블록(제품별×각도별) 검증.

★핵심 회귀 차단: 모든 제품×모든 각도×모든 줄에 🔴정보형 단어(교체방법·셀프·장착·청소
등)가 ★하나도 없어야 한다(구매형 지향). 정규화(공백 제거·소문자) 후 부분일치로 확인.
"""

from __future__ import annotations

from src.core.keyword_intent import INFO_KEYWORDS
from src.revu_form import (
    _subject_josa,
    default_mission_lines,
    mission_angles,
    mission_block,
)

# 에어컨필터·와이퍼·generic(그 외) 모두 커버.
_PRODUCTS = ["에어컨필터", "캐빈필터", "와이퍼", "방향제"]


def _norm(s: str) -> str:
    return s.replace(" ", "").lower()


_INFO_NORM = [_norm(w) for w in INFO_KEYWORDS]


def test_mission_block_three_nonempty_lines_all_products_angles():
    for prod in _PRODUCTS:
        for key, _lab in mission_angles(prod):
            lines = mission_block("EV5", prod, key)
            assert len(lines) == 3, f"{prod}/{key}: 3줄 아님"
            assert all(ln.strip() for ln in lines), f"{prod}/{key}: 빈 줄 존재"


def test_mission_block_has_no_info_keywords_anywhere():
    """★모든 제품×각도×줄에 정보형 단어가 하나도 없음(교체방법·장착 등 회귀 차단)."""
    for prod in _PRODUCTS:
        for key, _lab in mission_angles(prod):
            for line in mission_block("EV5", prod, key):
                low = _norm(line)
                for raw, info in zip(INFO_KEYWORDS, _INFO_NORM):
                    assert info not in low, (
                        f"{prod}/{key}: 정보형 '{raw}' 포함됨 → {line}")


def test_mission_block_contains_car_and_product():
    lines = mission_block("EV5", "에어컨필터", "smell")
    assert "EV5" in lines[0]
    assert "에어컨필터" in lines[0]


def test_mission_block_product_branch_differs():
    af = mission_block("EV5", "에어컨필터", "smell")
    wp = mission_block("EV5", "와이퍼", "noise")
    assert af != wp                       # 제품 분기로 내용 달라짐
    assert any("필터" in ln for ln in af)
    assert any("와이퍼" in ln for ln in wp)


def test_mission_angles_counts():
    assert len(mission_angles("에어컨필터")) >= 3
    assert len(mission_angles("와이퍼")) >= 3
    assert len(mission_angles("방향제")) >= 1   # generic(핵심가치·가성비) 2개


def test_mission_angles_returns_key_label_tuples():
    angles = mission_angles("와이퍼")
    assert all(isinstance(k, str) and isinstance(lab, str) for k, lab in angles)
    keys = {k for k, _ in angles}
    assert "noise" in keys and "water" in keys     # 와이퍼 각도 키 존재


def test_mission_block_no_car_returns_blank():
    assert mission_block("", "에어컨필터", "smell") == ["", "", ""]
    assert default_mission_lines("", "와이퍼") == ["", "", ""]


def test_mission_block_unknown_angle_falls_back_to_first():
    fallback = mission_block("EV5", "에어컨필터", "존재안함")
    first = mission_block("EV5", "에어컨필터", mission_angles("에어컨필터")[0][0])
    assert fallback == first


def test_default_mission_lines_uses_first_angle():
    assert default_mission_lines("EV5", "와이퍼") == mission_block(
        "EV5", "와이퍼", mission_angles("와이퍼")[0][0])


def test_generic_selling_is_placeholder_for_user_to_fill():
    """generic 셀링포인트는 사용자가 채울 자리표시(AI 생성 아님)."""
    lines = mission_block("EV5", "방향제", "value")
    assert "[제품 핵심 셀링포인트" in "".join(lines)


def test_subject_josa():
    assert _subject_josa("확보") == "가"      # 모음 끝(보) → 가
    assert _subject_josa("만족도") == "가"    # 도 → 가
    assert _subject_josa("가성비") == "가"    # 비 → 가
    assert _subject_josa("쾌적함") == "이"    # 받침 ㅁ → 이
    assert _subject_josa("성능") == "이"      # 받침 ㅇ → 이
    assert _subject_josa("채터링)") == "이"   # 괄호로 끝나도 마지막 한글 '링' → 이
    assert _subject_josa("") == "이"          # 한글 없음 → 이
    assert _subject_josa("ABC") == "이"       # 한글 없음 → 이


def test_mission2_no_vowel_plus_이_grammar_error():
    """전 제품×전 각도 미션2에 '확보이/만족도이/가성비이' 같은 모음+이 오류가 없다."""
    bad = ["확보이 ", "만족도이 ", "가성비이 ", "쾌적함가 ", "성능가 "]
    for prod in _PRODUCTS:
        for key, _lab in mission_angles(prod):
            m2 = mission_block("EV5", prod, key)[1]
            for token in bad:
                assert token not in m2, f"{prod}/{key}: 조사 오류 '{token.strip()}' → {m2}"


def test_richness_missions_are_long_enough():
    """3줄 미션이 빈약 템플릿이 아니라 리치(충분히 길다) — 5단 구조 압축."""
    lines = mission_block("EV5", "에어컨필터", "smell")
    assert len("".join(lines)) >= 300
