"""
csv_adapter.py — DataAdapter 의 CSV 구현.

입력: 오빠두 '네이버 검색광고 연관검색어 스크랩' 엑셀에서 내보낸 CSV.
  (네이버 검색광고 키워드도구 RelKwdStat 를 엑셀로 받아 내보낸 형태)

이 어댑터는 라이브 네이버 API(HMAC) 어댑터로 가기 전 '실데이터 검증' 단계다.
검증이 끝나면 동일한 DataAdapter 인터페이스로 라이브 어댑터를 끼운다(README 참조).

오빠두 엑셀 CSV 의 실제 형태(실파일 검증):
  - 헤더가 1행이 아니라 상단 빈 줄/제목행('전체 검색결과','필터' 등) 아래에 온다.
  - 헤더 셀 안에 줄바꿈 포함('월검색수\\n(PC)', '월평균노출\\n광고수').
  - 연관키워드 컬럼명이 '연간키워드'로 표기됨(오빠두 오기).
  - 좌측에 항목/값 필터패널 컬럼이 함께 들어와 데이터표는 그 오른쪽에서 시작.
따라서: 헤더를 자동탐지하고, 셀을 정규화(공백·줄바꿈 제거)해 매칭하며,
컬럼은 위치가 아니라 '정규화된 헤더명으로 찾은 인덱스'로 읽는다(필터패널은 자연히 무시).

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
import re
import statistics
import warnings
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

# 셀/헤더 정규화: 공백·줄바꿈을 모두 제거해 비교한다.
#   '월검색수\n(PC)' → '월검색수(PC)', ' 검색 키워드 ' → '검색키워드'.
_WS_RE = re.compile(r"\s+")


def _norm(cell: Optional[str]) -> str:
    """셀을 비교용으로 정규화: 모든 공백/줄바꿈 제거."""
    return _WS_RE.sub("", cell or "")


# 헤더 자동탐지 토큰(정규화 기준). 이 둘이 동시에 있는 첫 행을 헤더로 본다.
_HEADER_PRIMARY = "검색키워드"
_HEADER_SECONDARY = "경쟁정도"


def _cell(row: list[str], idx: int) -> str:
    """행에서 인덱스로 셀을 안전하게 꺼낸다(행 길이 초과 시 빈 문자열)."""
    return row[idx].strip() if 0 <= idx < len(row) else ""


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
    """연관키워드가 소모품인지 판정한다(근사 필터 + 기기 가드).

    1) 소모품 사전 토큰이 하나도 없으면 소모품 아님.
    2) 토큰이 있어도 연관어에 기기어(config.DEVICE_GUARD_KEYWORDS, 예 '청소기')가
       함께 있으면 기기일 수 있다 → 강한 부품형(config.CONSUMABLE_STRONG_KEYWORDS)이
       같이 있을 때만 소모품으로 유지한다.
       · '로봇청소기물걸레패드' → STRONG('물걸레패드') 있음 → 소모품.
       · '로봇청소기교체'     → STRONG 없음(약한 '교체'만) → 기기로 보고 제외.
    3) 기기어가 없으면 토큰 존재만으로 소모품.
    실데이터 검증 근거는 config.CONSUMABLE_KEYWORDS 주석 참조.
    """
    if not any(token in rel_keyword for token in config.CONSUMABLE_KEYWORDS):
        return False
    if any(g in rel_keyword for g in config.DEVICE_GUARD_KEYWORDS):
        return any(s in rel_keyword for s in config.CONSUMABLE_STRONG_KEYWORDS)
    return True


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
    def _read_rows(self) -> list[list[str]]:
        """CSV 전체를 raw 행 리스트(list[list[str]])로 읽는다. 인코딩 후보를 순차 시도.

        헤더가 1행에 있다고 가정하지 않으므로 DictReader 가 아니라 csv.reader 로
        모든 논리행을 그대로 읽는다(상단 빈 줄/제목행/필터패널 포함).
        """
        if not os.path.exists(self.csv_path):
            raise FileNotFoundError(f"CSV 파일을 찾을 수 없습니다: {self.csv_path}")
        last_err: Optional[Exception] = None
        for enc in config.CSV_ENCODINGS:
            try:
                with open(self.csv_path, "r", encoding=enc, newline="") as f:
                    return list(csv.reader(f))
            except (UnicodeDecodeError, UnicodeError) as e:
                last_err = e
                continue
        raise UnicodeError(
            f"CSV 인코딩을 해석하지 못했습니다(시도: {config.CSV_ENCODINGS}): {self.csv_path}"
        ) from last_err

    def _detect_header(self, rows: list[list[str]]) -> int:
        """헤더 행의 인덱스를 자동탐지한다(1행 고정 가정 제거).

        규칙(정상 경로): 셀을 정규화(공백·줄바꿈 제거)했을 때 '검색키워드'와
        '경쟁정도'가 동시에 있는 첫 행을 헤더로 본다. 상단 빈 줄/제목행을 자연히
        건너뛴다. 오빠두 표준 형식은 항상 이 경로로 잡힌다.

        폴백 경로: 둘 다 만족하는 행이 없으면 '검색키워드'만 있는 첫 행을 헤더
        후보로 잡아 _resolve_columns 가 '어떤 논리 컬럼이 없는지' 구체적으로
        보고하게 한다. 폴백은 비표준 CSV 신호이므로 warnings.warn 으로 그 사실을
        남긴다 → 나중에 다른 CSV 가 폴백으로 잡히면 경고로 확인할 수 있다.
        그것도 없으면 CSVColumnError.
        """
        fallback: Optional[int] = None
        for i, row in enumerate(rows):
            normed = {_norm(c) for c in row}
            if _HEADER_PRIMARY in normed and _HEADER_SECONDARY in normed:
                # 정상 경로: 동시 조건 충족. (조용히 진행 — 로그 소음 방지)
                return i
            if fallback is None and _HEADER_PRIMARY in normed:
                fallback = i
        if fallback is not None:
            warnings.warn(
                f"CSV 헤더를 폴백 경로로 탐지했습니다: '{_HEADER_PRIMARY}'만 있고 "
                f"'{_HEADER_SECONDARY}'가 없는 {fallback}번 행을 헤더로 사용합니다. "
                f"오빠두 표준 형식이 아닐 수 있으니 컬럼 매핑을 확인하세요: {self.csv_path}",
                stacklevel=2,
            )
            return fallback
        raise CSVColumnError(
            f"CSV 헤더 행을 찾지 못했습니다('{_HEADER_PRIMARY}' 포함 행 없음): {self.csv_path}"
        )

    def _resolve_columns(self, header: list[str]) -> dict:
        """
        config.CSV_COLUMNS 의 논리 키 → 헤더 컬럼 '인덱스' 매핑을 만든다.

        헤더 셀과 별칭을 모두 정규화(공백·줄바꿈 제거)한 뒤 비교하므로
        '월검색수\\n(PC)' 같은 줄바꿈 헤더도 매칭된다. 위치가 아니라 헤더명으로
        인덱스를 찾기 때문에 좌측 필터패널 컬럼(어떤 별칭과도 안 맞음)은 무시된다.
        하나라도 못 찾으면 CSVColumnError(어떤 논리 컬럼이 없는지 명시).
        """
        normed_header = [_norm(c) for c in header]
        resolved: dict[str, int] = {}
        missing: list[str] = []
        for logical, aliases in config.CSV_COLUMNS.items():
            idx: Optional[int] = None
            for alias in aliases:
                na = _norm(alias)
                if na in normed_header:
                    idx = normed_header.index(na)
                    break
            if idx is None:
                missing.append(f"{logical}({'/'.join(aliases)})")
            else:
                resolved[logical] = idx
        if missing:
            raise CSVColumnError(
                "CSV 필수 컬럼 누락: "
                + ", ".join(missing)
                + f" | CSV 헤더: {header}"
            )
        return resolved

    # ----- 인터페이스 구현 -----
    def fetch_category_observations(self) -> list[CategoryObservation]:
        rows = self._read_rows()
        header_idx = self._detect_header(rows)
        cols = self._resolve_columns(rows[header_idx])
        data_rows = rows[header_idx + 1:]

        # 검색키워드(베이스 기기)별로 소모품 연관키워드 행을 모은다.
        grouped: dict[str, list[list[str]]] = defaultdict(list)
        for row in data_rows:
            base = _cell(row, cols["search_keyword"])
            rel = _cell(row, cols["rel_keyword"])
            if not base or not rel:  # 검색키워드/연관키워드 비면 skip(필터패널 행 포함)
                continue
            if _is_consumable(rel):
                grouped[base].append(row)

        observations: list[CategoryObservation] = []
        for base, consumable_rows in grouped.items():
            if not consumable_rows:
                continue  # 소모품 연관키워드가 없으면 카테고리 후보 아님

            # 신호 7: 절대 월검색수 합(PC + 모바일).
            search_volume = sum(
                _parse_volume(_cell(r, cols["monthly_pc"]))
                + _parse_volume(_cell(r, cols["monthly_mobile"]))
                for r in consumable_rows
            )

            # 신호 8: 평균 노출광고수 + 대표 경쟁정도.
            ad_depths = [_parse_volume(_cell(r, cols["avg_ad_depth"])) for r in consumable_rows]
            avg_ad_depth = statistics.mean(ad_depths) if ad_depths else None
            comp_values = [_cell(r, cols["comp_idx"]) for r in consumable_rows]
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
