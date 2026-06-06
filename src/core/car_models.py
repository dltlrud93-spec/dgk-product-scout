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


def normalize_text(s: str) -> str:
    return _STRIP_RE.sub("", str(s or "")).upper()


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
        # 1) 세대 포함 별칭(긴 것 우선).
        for al in self._aliases_desc:
            if al and al in residual:
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
