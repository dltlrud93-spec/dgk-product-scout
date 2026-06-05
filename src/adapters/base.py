"""
base.py — 데이터 어댑터 인터페이스.

스펙 12절 #1("소스별 실제 수집 방식 — 쿠팡·1688 을 공식 API / 자동 스크래핑 /
수동 중 무엇으로")은 P1 착수 시점에 아직 미확정이다.

따라서 P1 에서는 '수집부가 지켜야 할 계약(인터페이스)'만 정의하고,
실제 스크래핑/API 호출/수수료 값은 구현하지 않는다.
임의의 스크래핑 방식이나 수수료 값을 지어내지 않는다.

12절 #1·#2 가 확정되면 이 인터페이스를 구현하는 실제 어댑터
(예: CoupangApiAdapter, Naver1688Adapter)로 MockAdapter 를 교체한다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.schema import CategoryObservation


class DataAdapter(ABC):
    """
    카테고리 발굴에 필요한 외부 데이터를 공급하는 어댑터 계약.

    구현체는 마켓 베스트셀러/데이터랩/1688/네이버 검색광고 등에서
    데이터를 모아 CategoryObservation 리스트로 반환할 책임만 진다.
    신호 산출/4분면 판정/랭킹은 어댑터 책임이 아니다(signals.py 등에서 수행).
    """

    @abstractmethod
    def fetch_category_observations(self) -> list[CategoryObservation]:
        """
        발굴 대상 카테고리 후보들의 원시 관측치를 반환한다.

        실제 구현(미확정, 12절 #1)에서는 소스별 수집 → 정규화 →
        CategoryObservation 매핑을 수행해야 한다.
        """
        raise NotImplementedError
