"""
car_models.py — 차종 인식·정규화 사전 (Phase C-1).

★용도: Phase C 차종 스캐너가 수확한 키워드에서 '진짜 한국 자동차 모델'을 걸러내고
정규명으로 통일하는 인식·정규화 전용. **검색 시드 아님.** (Phase B 의 소모품 사전 함정과
본질이 다름 — 그건 '무엇이 관련 있는가'(무한·가변)였고, 이건 '무엇이 차 모델인가'(유한·닫힌집합).)

원칙(스펙 C-3):
  · 세대 분리 유지 — 아반떼MD ≠ 아반떼AD (다른 부품 = 다른 상품).
  · 세대 없는 bare 모델명("아반떼")은 임의 귀속 금지 → 별도 모호 버킷 "{family}(세대미상)".
  · 충돌 시 더 구체적인(세대 포함, 더 긴) 별칭 우선.

데이터는 data/car_models.json(초안). 시경 교정본은 이 JSON 만 교체하면 된다(코드 무수정).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

_DATA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "car_models.json",
)

# 정규화: 공백·하이픈 제거 + 영문 대문자화(한글은 영향 없음). "쏘렌토 MQ4"="쏘렌토MQ4", "CR-V"="CRV".
_STRIP_RE = re.compile(r"[\s\-]+")

# 단축 별칭 오매칭 가드: 길이 ≤ 이 값인 별칭은 'substring' 이 아니라 '토큰 경계' 매칭만 인정.
# 짧은 별칭(마스터·레이·G80·A4 등)이 더 긴 비차종어(마스터실린더·디스플레이…)의 부분문자열로
# 잘못 잡히는 것을 막는다. 긴 별칭(세대 포함 등)은 substring 으로 둔다(꼬리 표기 흡수).
SHORT_ALIAS_MAXLEN = 3


def normalize_text(s: str) -> str:
    return _STRIP_RE.sub("", str(s or "")).upper()


def _boundary_contains(needle: str, hay: str) -> bool:
    """needle 이 hay 안에 '영숫자(한글 포함) 경계'로 등장하는가. 양옆이 글자면 거부.

    한글 음절은 str.isalnum()==True 라, '마스터실린더'에서 '마스터'는 우측이 '실'(글자)→거부.
    '마스터'(잔여=별칭) 또는 'G80'(앞뒤 경계)처럼 경계에 닿을 때만 인정.
    """
    n = len(needle)
    start = 0
    while True:
        i = hay.find(needle, start)
        if i < 0:
            return False
        j = i + n
        left_ok = (i == 0) or (not hay[i - 1].isalnum())
        right_ok = (j == len(hay)) or (not hay[j].isalnum())
        if left_ok and right_ok:
            return True
        start = i + 1


@dataclass
class Recognition:
    """인식 결과. canonical=None 이면 미인식(버림). ambiguous=True 면 세대미상 모호 버킷."""
    canonical: str | None
    ambiguous: bool
    matched: str | None   # 매칭된 별칭(정규화형) 또는 bare family

    @property
    def recognized(self) -> bool:
        return self.canonical is not None


class CarModelIndex:
    def __init__(self, data: dict):
        self.part_words = sorted({normalize_text(w) for w in data.get("part_words", [])},
                                 key=len, reverse=True)
        self.bare_families: list[str] = data.get("bare_families", [])
        self.alias_to_canonical: dict[str, str] = {}
        self.maker_of: dict[str, str] = {}
        self.note_of: dict[str, str] = {}
        for m in data.get("models", []):
            canon = m["canonical"]
            self.maker_of[canon] = m.get("maker", "")
            self.note_of[canon] = m.get("note", "")
            for al in m["aliases"]:
                # 같은 정규화 별칭이 여러 모델에 있으면 첫 등록 우선(데이터 정합은 사전에서 관리).
                self.alias_to_canonical.setdefault(normalize_text(al), canon)
        # 긴 별칭 우선 매칭(세대 포함이 bare 보다 먼저).
        self._aliases_desc = sorted(self.alias_to_canonical, key=len, reverse=True)
        self._bare_desc = sorted(self.bare_families, key=lambda f: len(normalize_text(f)),
                                 reverse=True)

    @property
    def model_count(self) -> int:
        return len(self.maker_of)

    def merge_aliases(self, names) -> "CarModelIndex":
        """데이터(조견표) 차종명을 인식 별칭으로 병합(라이브 동기화). self 반환.

        · canonical = 차종명 원문(그대로).
        · 이미 매핑된 정규화 별칭은 건드리지 않는다 → ★기존 JSON 항목 우선(충돌 시 JSON 승).
        · 새 별칭만 추가하고 매칭 인덱스(_aliases_desc)를 1회 재정렬.

        효과: JSON 초안에 없는 신차(데이터에 등록됨)를 차종 수요 화면이 인식하게 된다.
        시트 읽기 자체는 호출부가 담당(여기는 순수 — 이름 리스트만 받음).
        """
        added = False
        for name in names or []:
            canon = str(name or "").strip()
            if not canon:
                continue
            norm = normalize_text(canon)
            if not norm or norm in self.alias_to_canonical:
                continue  # 빈 별칭 또는 기존(JSON) 우선 → skip
            self.alias_to_canonical[norm] = canon
            self.maker_of.setdefault(canon, "")
            self.note_of.setdefault(canon, "")
            added = True
        if added:
            self._aliases_desc = sorted(self.alias_to_canonical, key=len, reverse=True)
        return self

    def strip_parts(self, keyword: str) -> str:
        """부품어 제거(정규화형 반환). 모델같은 잔여 토큰 식별·bare 검출 보조용."""
        s = normalize_text(keyword)
        for pw in self.part_words:
            s = s.replace(pw, "")
        return s

    def recognize(self, keyword: str) -> Recognition:
        """키워드 → 정규명. 스펙 순서대로 '부품어 제거 → 별칭 매칭'. 세대 별칭 우선,
        없으면 bare family 모호 버킷, 그래도 없으면 미인식.

        부품어를 먼저 떼는 이유: 부품어 자체가 모델 별칭을 substring 으로 품을 수 있다
        (예: '와이퍼블레이드' 안의 '레이'). 떼고 매칭해야 오매칭을 막는다.
        """
        residual = self.strip_parts(keyword)
        # 1) 세대 포함 별칭(긴 것 우선). 짧은 별칭은 토큰 경계 매칭만 인정(오매칭 가드).
        for al in self._aliases_desc:
            if not al:
                continue
            hit = (_boundary_contains(al, residual) if len(al) <= SHORT_ALIAS_MAXLEN
                   else al in residual)
            if hit:
                return Recognition(self.alias_to_canonical[al], False, al)
        # 2) bare family → 모호 버킷(임의 세대 귀속 금지).
        for fam in self._bare_desc:
            if normalize_text(fam) in residual:
                return Recognition(f"{fam}(세대미상)", True, fam)
        # 3) 미인식.
        return Recognition(None, False, None)


def load_car_models(path: str | None = None) -> CarModelIndex:
    with open(path or _DATA_PATH, encoding="utf-8") as f:
        return CarModelIndex(json.load(f))
