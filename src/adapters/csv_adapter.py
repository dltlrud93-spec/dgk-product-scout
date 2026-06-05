"""
csv_adapter.py — DataAdapter 의 CSV 구현.

입력: 오빠두 '네이버 검색광고 연관검색어 스크랩' 엑셀에서 내보낸 CSV.
  (네이버 검색광고 키워드도구 RelKwdStat 를 엑셀로 받아 내보낸 형태)

이 어댑터는 라이브 네이버 API(HMAC) 어댑터로 가기 전 '실데이터 검증' 단계다.
검증이 끝나면 동일한 DataAdapter 인터페이스로 라이브 어댑터를 끼운다(README 참조).

매핑(스펙 3.2):
  - 신호 7(시장 규모) = 월검색수(PC) + 월검색수(모바일)  ── 절대 검색수
  - 신호 8(광고싸움)  = 경쟁정도(compIdx) + 월평균노출광고수(plAvgDepth)

처리 흐름:
  검색키워드(베이스 기기)별로 묶고 → 연관키워드를 소모품 사전으로 필터 →
  남은 소모품 행을 집계해 카테고리 후보 1건(CategoryObservation)을 만든다.
  category_name = "{검색키워드} 호환 소모품".

⚠️ 소모품 필터는 부분 문자열 매칭이라 근사다(오매칭 가능). 결과는 사람 확인 필요.
"""

from __future__ import annotations

import csv
import os
import statistics
from collections import defaultdict
from typing import Optional

import config
from src.adapters.base import DataAdapter
from src.schema import CategoryObservation


class CSVColumnError(ValueError):
    """CSV 에 필수 컬럼이 없을 때. 어떤 컬럼이 없는지 메시지에 담는다."""


# 경쟁정도(compIdx) ↔ 서수 매핑(대표값 집계용).
_COMP_ORDINAL = {"낮음": 0, "중간": 1, "높음": 2}
_ORDINAL_COMP = {v: k for k, v in _COMP_ORDINAL.items()}


def _parse_volume(raw: Optional[str]) -> float:
    """
    검색수/노출수 문자열을 숫자로 파싱한다.

    규칙(보완사항 1):
      - 천단위 콤마 제거.
      - "< 10" 같은 부등호(저소) 표기 → 보수적으로 config.LOW_VOLUME_FALLBACK 로 치환.
        (임계 미만을 과대평가하지 않기 위함)
      - 빈 칸/파싱 불가 → 동일하게 LOW_VOLUME_FALLBACK.
    숨은 숫자를 코드에 두지 않으려고 대입값은 config 상수를 쓴다.
    """
    if raw is None:
        return config.LOW_VOLUME_FALLBACK
    s = str(raw).strip().replace(",", "")
    if not s:
        return config.LOW_VOLUME_FALLBACK
    if "<" in s:  # "< 10", "<10" 등 저소 표기
        return config.LOW_VOLUME_FALLBACK
    try:
        return float(s)
    except ValueError:
        return config.LOW_VOLUME_FALLBACK


def _is_consumable(rel_keyword: str) -> bool:
    """연관키워드에 소모품 사전 토큰이 포함되면 True(근사 필터)."""
    return any(token in rel_keyword for token in config.CONSUMABLE_KEYWORDS)


def _aggregate_comp_idx(values: list[str]) -> Optional[str]:
    """경쟁정도 라벨들을 서수 평균 후 반올림해 대표 라벨로 환원."""
    ordinals = [_COMP_ORDINAL[v] for v in values if v in _COMP_ORDINAL]
    if not ordinals:
        return None
    avg = round(statistics.mean(ordinals))
    return _ORDINAL_COMP[avg]


class CSVAdapter(DataAdapter):
    """오빠두 연관검색어 CSV → CategoryObservation 리스트."""

    def __init__(self, csv_path: str):
        self.csv_path = csv_path

    # ----- CSV 읽기 -----
    def _read_rows(self) -> tuple[list[dict], list[str]]:
        """CSV 를 읽어 (행 리스트, 헤더 리스트) 반환. 인코딩 후보를 순차 시도."""
        if not os.path.exists(self.csv_path):
            raise FileNotFoundError(f"CSV 파일을 찾을 수 없습니다: {self.csv_path}")
        last_err: Optional[Exception] = None
        for enc in config.CSV_ENCODINGS:
            try:
                with open(self.csv_path, "r", encoding=enc, newline="") as f:
                    reader = csv.DictReader(f)
                    rows = list(reader)
                    return rows, (reader.fieldnames or [])
            except (UnicodeDecodeError, UnicodeError) as e:
                last_err = e
                continue
        raise UnicodeError(
            f"CSV 인코딩을 해석하지 못했습니다(시도: {config.CSV_ENCODINGS}): {self.csv_path}"
        ) from last_err

    def _resolve_columns(self, header: list[str]) -> dict:
        """
        config.CSV_COLUMNS 의 논리 키 → 실제 헤더명 매핑을 만든다.
        하나라도 못 찾으면 CSVColumnError(어떤 논리 컬럼이 없는지 명시).
        """
        resolved: dict[str, str] = {}
        missing: list[str] = []
        header_set = set(header)
        for logical, aliases in config.CSV_COLUMNS.items():
            match = next((a for a in aliases if a in header_set), None)
            if match is None:
                missing.append(f"{logical}({'/'.join(aliases)})")
            else:
                resolved[logical] = match
        if missing:
            raise CSVColumnError(
                "CSV 필수 컬럼 누락: "
                + ", ".join(missing)
                + f" | CSV 헤더: {header}"
            )
        return resolved

    # ----- 인터페이스 구현 -----
    def fetch_category_observations(self) -> list[CategoryObservation]:
        rows, header = self._read_rows()
        cols = self._resolve_columns(header)

        # 검색키워드(베이스 기기)별로 소모품 연관키워드 행을 모은다.
        grouped: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            base = (row.get(cols["search_keyword"]) or "").strip()
            rel = (row.get(cols["rel_keyword"]) or "").strip()
            if not base or not rel:
                continue
            if _is_consumable(rel):
                grouped[base].append(row)

        observations: list[CategoryObservation] = []
        for base, consumable_rows in grouped.items():
            if not consumable_rows:
                continue  # 소모품 연관키워드가 없으면 카테고리 후보 아님

            # 신호 7: 절대 월검색수 합(PC + 모바일).
            search_volume = sum(
                _parse_volume(r.get(cols["monthly_pc"]))
                + _parse_volume(r.get(cols["monthly_mobile"]))
                for r in consumable_rows
            )

            # 신호 8: 평균 노출광고수 + 대표 경쟁정도.
            ad_depths = [_parse_volume(r.get(cols["avg_ad_depth"])) for r in consumable_rows]
            avg_ad_depth = statistics.mean(ad_depths) if ad_depths else None
            comp_values = [
                (r.get(cols["comp_idx"]) or "").strip() for r in consumable_rows
            ]
            comp_idx = _aggregate_comp_idx(comp_values)

            observations.append(
                CategoryObservation(
                    category_name=f"{base} 호환 소모품",
                    discovery_pattern="호환소모품",
                    # CSV 에 없는 신호(1·3·4·5)는 None/0 → 보수적으로 0점 처리됨.
                    base_device_bestseller_rank=None,
                    base_device_search_volume=None,
                    has_consumable=True,
                    oem_price_krw=None,
                    compatible_price_krw=None,
                    repurchase_cycle_days=None,
                    compatible_seller_count=None,
                    # 신호 6: 스펙상 "거의 항상 충족" → True 가정(문서화된 가정).
                    oem_producible=True,
                    # 신호 7 입력(절대 검색량).
                    category_search_volume=int(search_volume),
                    # 신호 8 입력.
                    comp_idx=comp_idx,
                    avg_ad_depth=avg_ad_depth,
                )
            )
        return observations
