"""
test_car_models_merge.py — 인식 사전 라이브 동기화(merge_aliases) 규칙 검증.

데이터(조견표) 차종명을 인식 인덱스에 별칭으로 병합하되:
  · canonical = 차종명 원문.
  · 기존 JSON 항목과 충돌하면 JSON 우선(새 별칭만 추가).
  · 빈/None 입력은 무변화(시트 실패 시 JSON 폴백과 동치).
"""

from __future__ import annotations

from src.core.car_models import CarModelIndex


def _idx() -> CarModelIndex:
    return CarModelIndex({
        "part_words": ["에어컨필터"],
        "bare_families": [],
        "models": [
            {"canonical": "셀토스(구형)", "aliases": ["셀토스"], "maker": "기아"},
        ],
    })


def test_merge_adds_new_alias_and_recognizes():
    idx = _idx()
    idx.merge_aliases(["그랑콜레오스"])
    rec = idx.recognize("그랑콜레오스에어컨필터")
    assert rec.recognized
    assert rec.canonical == "그랑콜레오스"   # canonical = 차종명 원문


def test_merge_json_wins_on_conflict():
    """정규화가 기존 JSON 별칭과 충돌하면 JSON canonical 유지(덮어쓰지 않음)."""
    idx = _idx()
    idx.merge_aliases(["셀토스"])   # JSON 에 이미 '셀토스' → '셀토스(구형)'
    assert idx.alias_to_canonical["셀토스"] == "셀토스(구형)"   # JSON 우선
    rec = idx.recognize("셀토스에어컨필터")
    assert rec.canonical == "셀토스(구형)"


def test_merge_empty_or_none_is_noop():
    idx = _idx()
    before = dict(idx.alias_to_canonical)
    idx.merge_aliases([])
    idx.merge_aliases(None)
    idx.merge_aliases(["", "   "])   # 빈 문자열은 건너뜀
    assert idx.alias_to_canonical == before   # 시트 실패 폴백 = 무변화


def test_merge_returns_self_for_chaining():
    idx = _idx()
    assert idx.merge_aliases(["레이"]) is idx
