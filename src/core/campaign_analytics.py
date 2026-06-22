"""
campaign_analytics.py — 스마트스토어 마케팅분석 표(붙여넣기)를 파싱·집계하는 순수 함수.

streamlit 의존 없음(테스트 가능). 입력은 스마트스토어 '마케팅분석'에서 표를 복사한
raw 텍스트(탭 구분, 헤더 여러 줄 포함 가능).

컬럼 고정 인덱스(검증됨):
  0=채널속성(전체/모바일/PC)  2=nt_medium  3=nt_detail  4=nt_keyword
  6=유입수  9=결제수(마지막클릭)  11=결제금액(마지막클릭)

데이터행 판별: 탭 분리 후 len>=12 이고 col[0] in {전체,모바일,PC} 이며 col[6]이 숫자인 줄만.
  (헤더가 몇 줄이든 이 규칙이 자동으로 걸러낸다.)

집계 원칙:
  · summary = '전체' 행 합(기기 전체 그랜드토탈). '전체' 행이 없으면 기기(모바일/PC)행 합.
  · by_medium / by_campaign / by_product = ★기기행(모바일+PC)만 합산('전체'는 그랜드토탈이라
    중복집계 방지로 제외). by_campaign 은 nt_detail 로 모바일+PC 를 병합한다.
"""

from __future__ import annotations

import re

# 성과 임계값(결제율 %) — 조정 가능.
PERF_GOOD_RATE = 15.0
PERF_BAD_RATE = 5.0

_DEVICE_ALL = "전체"
_DEVICE_SPLIT = ("모바일", "PC")
_CHANNELS = {_DEVICE_ALL, *_DEVICE_SPLIT}

# 컬럼 인덱스.
_C_CHANNEL, _C_MEDIUM, _C_DETAIL, _C_KEYWORD = 0, 2, 3, 4
_C_INFLOW, _C_PAY, _C_AMOUNT = 6, 9, 11
_MIN_COLS = 12

# 구형 detail → 제품 추정용 키워드(부분일치, 더 구체적인 것 먼저).
_PRODUCT_SUBSTR = [
    (("에어로닷", "pump", "펌프"), "에어로닷"),
    (("거치대", "holder"), "거치대"),
    (("네비", "navi", "필름", "film"), "네비필름"),
    (("유리복원", "glass"), "유리복원제"),
    (("와이퍼", "wiper"), "와이퍼"),
    (("필터", "filter"), "에어컨필터"),
]


def _num(s: str) -> float:
    """콤마·% 제거 후 float. 비거나 못 읽으면 0.0."""
    s = (s or "").replace(",", "").replace("%", "").strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _is_num(s: str) -> bool:
    """콤마·% 제거 후 숫자로 읽히면 True(빈 문자열은 False)."""
    s = (s or "").replace(",", "").replace("%", "").strip()
    if not s:
        return False
    try:
        float(s)
        return True
    except ValueError:
        return False


def _rate(pay: float, inflow: float) -> float:
    """결제율(%) — 유입 0 방어."""
    return (pay / inflow * 100.0) if inflow else 0.0


def _perf_tag(rate: float) -> str:
    if rate >= PERF_GOOD_RATE:
        return "good"
    if rate < PERF_BAD_RATE:
        return "bad"
    return "mid"


def _is_new_detail(detail: str) -> bool:
    """신규형 detail(`날짜_제품_차종`) — '_'로 3토큰 이상."""
    return detail.count("_") >= 2


def _detect_product(detail: str) -> str:
    """구형 detail 에서 제품 키워드 substring 감지. 못 찾으면 '미분류'."""
    low = detail.lower()
    for needles, product in _PRODUCT_SUBSTR:
        if any(n.lower() in low for n in needles):
            return product
    return "미분류"


_C_SOURCE = 1


class _Row:
    __slots__ = (
        "channel", "source", "medium", "detail", "keyword", "inflow", "pay", "amount")

    def __init__(self, cols: list[str]):
        self.channel = cols[_C_CHANNEL].strip()
        # nt_source(col1) — 인덱스 범위 방어(없으면 "").
        self.source = cols[_C_SOURCE].strip() if len(cols) > _C_SOURCE else ""
        self.medium = cols[_C_MEDIUM].strip()
        self.detail = cols[_C_DETAIL].strip()
        self.keyword = cols[_C_KEYWORD].strip()
        self.inflow = _num(cols[_C_INFLOW])
        self.pay = _num(cols[_C_PAY])
        self.amount = _num(cols[_C_AMOUNT])


def _parse_rows(raw: str) -> list[_Row]:
    """raw → 데이터행만 _Row 리스트(헤더·깨진 줄 자동 제외)."""
    rows: list[_Row] = []
    for line in (raw or "").splitlines():
        if "\t" not in line:
            continue
        cols = line.split("\t")
        if len(cols) < _MIN_COLS:
            continue
        if cols[_C_CHANNEL].strip() not in _CHANNELS:
            continue
        if not _is_num(cols[_C_INFLOW]):
            continue
        rows.append(_Row(cols))
    return rows


def _aggregate(rows: list[_Row], key_fn, key_name: str, *, with_tag: bool = False) -> list[dict]:
    """key_fn(row) 로 그룹 합산 → [{key_name, inflow, pay, rate, amount[, tag]}], rate 내림차순."""
    groups: dict[str, dict] = {}
    order: list[str] = []
    for r in rows:
        k = key_fn(r)
        if k not in groups:
            groups[k] = {"inflow": 0.0, "pay": 0.0, "amount": 0.0}
            order.append(k)
        g = groups[k]
        g["inflow"] += r.inflow
        g["pay"] += r.pay
        g["amount"] += r.amount
    out: list[dict] = []
    for k in order:
        g = groups[k]
        rate = _rate(g["pay"], g["inflow"])
        item = {key_name: k, "inflow": g["inflow"], "pay": g["pay"],
                "rate": rate, "amount": g["amount"]}
        if with_tag:
            item["tag"] = _perf_tag(rate)
        out.append(item)
    out.sort(key=lambda d: d["rate"], reverse=True)
    return out


def _build_warnings(device_rows: list[_Row]) -> list[str]:
    warnings: list[str] = []

    # ① nt_medium 대소문자 혼용
    spellings: dict[str, set] = {}
    for r in device_rows:
        if r.medium:
            spellings.setdefault(r.medium.lower(), set()).add(r.medium)
    if any(len(v) >= 2 for v in spellings.values()):
        warnings.append("nt_medium 대소문자 혼용(예: REVU/revu) — 분리 집계됨")

    # ② nt_keyword 빈 비율
    empty_kw = sum(1 for r in device_rows if r.keyword in ("", "-"))
    if empty_kw > 0:
        warnings.append(f"nt_keyword {empty_kw}개 비어있음 — 추적 약화")

    # ③ nt_detail 형식 혼용
    has_new = any(_is_new_detail(r.detail) for r in device_rows if r.detail)
    has_old = any((not _is_new_detail(r.detail)) for r in device_rows if r.detail)
    if has_new and has_old:
        warnings.append("nt_detail 형식 2종 혼용 — 규칙 통일 필요")

    return warnings


def parse_smartstore_table(raw: str) -> dict:
    """스마트스토어 마케팅분석 표 raw → 집계 dict.

    반환 키: summary, by_medium, by_campaign, by_product, warnings.
    """
    rows = _parse_rows(raw)
    total_rows = [r for r in rows if r.channel == _DEVICE_ALL]
    device_rows = [r for r in rows if r.channel in _DEVICE_SPLIT]

    # summary — '전체'행 합(없으면 기기행 합).
    src = total_rows if total_rows else device_rows
    s_inflow = sum(r.inflow for r in src)
    s_pay = sum(r.pay for r in src)
    s_amount = sum(r.amount for r in src)
    summary = {
        "inflow": s_inflow,
        "pay": s_pay,
        "pay_rate": _rate(s_pay, s_inflow),
        "amount": s_amount,
    }

    by_medium = _aggregate(device_rows, lambda r: r.medium.lower(), "medium")
    by_campaign = _aggregate(
        device_rows, lambda r: r.detail, "campaign", with_tag=True)

    # by_product — 신규형 행이 있을 때만(신규형=토큰[1], 구형=substring).
    has_new = any(_is_new_detail(r.detail) for r in device_rows if r.detail)
    if has_new:
        def _product_key(r: _Row) -> str:
            if _is_new_detail(r.detail):
                return r.detail.split("_")[1]
            return _detect_product(r.detail)
        by_product = _aggregate(device_rows, _product_key, "product")
    else:
        by_product = []

    # 파싱된 전체 데이터행(전체+기기, 붙여넣기 순서) — 엑셀 시트1 재현용.
    parsed_rows = [
        {
            "channel": r.channel, "source": r.source, "medium": r.medium,
            "detail": r.detail, "keyword": r.keyword, "inflow": r.inflow,
            "pay": r.pay, "pay_rate": _rate(r.pay, r.inflow), "amount": r.amount,
        }
        for r in rows
    ]

    return {
        "summary": summary,
        "by_medium": by_medium,
        "by_campaign": by_campaign,
        "by_product": by_product,
        "warnings": _build_warnings(device_rows),
        "rows": parsed_rows,
    }


# ── 엑셀(xlsx) 내보내기 — 2시트(원본붙여넣기 + 분석결과) ─────────────────────
# 결제율은 ★두 칸(표시=소수1자리 / 정확=풀소수)으로 — 엑셀 재검산 시 반올림 오차 방지.
_FMT_INT = "#,##0"
_FMT_RATE_SHOW = "0.0"
_FMT_RATE_EXACT = "0.000000"


def _write_row(ws, values, *, formats=None):
    """ws 에 한 행 추가하고, formats(컬럼인덱스→number_format) 지정."""
    ws.append(list(values))
    if formats:
        r = ws.max_row
        for col_idx, fmt in formats.items():
            ws.cell(row=r, column=col_idx + 1).number_format = fmt


def build_analytics_xlsx(result: dict) -> bytes:
    """집계 result → xlsx bytes(시트2: 원본붙여넣기 + 분석결과). 순수 함수."""
    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()

    # ── 시트1: 원본붙여넣기(파싱된 전체 데이터행) ──
    ws1 = wb.active
    ws1.title = "원본붙여넣기"
    ws1.append([
        "채널속성", "nt_source", "nt_medium", "nt_detail", "nt_keyword",
        "유입", "결제", "결제율_표시", "결제율_정확", "결제금액",
    ])
    row_fmt = {5: _FMT_INT, 6: _FMT_INT, 7: _FMT_RATE_SHOW,
               8: _FMT_RATE_EXACT, 9: _FMT_INT}
    for r in result.get("rows", []):
        _write_row(ws1, [
            r["channel"], r["source"], r["medium"], r["detail"], r["keyword"],
            r["inflow"], r["pay"], round(r["pay_rate"], 1), r["pay_rate"], r["amount"],
        ], formats=row_fmt)

    # ── 시트2: 분석결과(섹션 세로 stack) ──
    ws2 = wb.create_sheet("분석결과")
    s = result["summary"]

    ws2.append(["[요약]"])
    _write_row(ws2, ["총 유입", s["inflow"]], formats={1: _FMT_INT})
    _write_row(ws2, ["총 결제", s["pay"]], formats={1: _FMT_INT})
    _write_row(ws2, ["결제율_표시", round(s["pay_rate"], 1)], formats={1: _FMT_RATE_SHOW})
    _write_row(ws2, ["결제율_정확", s["pay_rate"]], formats={1: _FMT_RATE_EXACT})
    _write_row(ws2, ["결제금액", s["amount"]], formats={1: _FMT_INT})

    ws2.append([])
    ws2.append(["[데이터 품질 경고]"])
    warnings = result.get("warnings") or []
    if warnings:
        for w in warnings:
            ws2.append([w])
    else:
        ws2.append(["규칙 위반 없음"])

    # 매체/캠페인/제품 공통 숫자 포맷(헤더 다음 데이터행에 적용).
    agg_fmt = {1: _FMT_INT, 2: _FMT_INT, 3: _FMT_RATE_SHOW,
               4: _FMT_RATE_EXACT, 5: _FMT_INT}

    ws2.append([])
    ws2.append(["[매체 통합 집계]"])
    ws2.append(["매체", "유입", "결제", "결제율_표시", "결제율_정확", "결제금액"])
    for m in result.get("by_medium", []):
        _write_row(ws2, [
            m["medium"], m["inflow"], m["pay"], round(m["rate"], 1), m["rate"],
            m["amount"],
        ], formats=agg_fmt)

    ws2.append([])
    ws2.append(["[캠페인별 성과]"])
    ws2.append(["캠페인", "유입", "결제", "결제율_표시", "결제율_정확", "결제금액", "태그"])
    _tag_kr = {"good": "우수", "bad": "저조", "mid": ""}
    for c in result.get("by_campaign", []):
        _write_row(ws2, [
            c["campaign"], c["inflow"], c["pay"], round(c["rate"], 1), c["rate"],
            c["amount"], _tag_kr.get(c.get("tag", ""), ""),
        ], formats=agg_fmt)

    if result.get("by_product"):
        ws2.append([])
        ws2.append(["[제품별 성과]"])
        ws2.append(["제품", "유입", "결제", "결제율_표시", "결제율_정확", "결제금액"])
        for p in result["by_product"]:
            _write_row(ws2, [
                p["product"], p["inflow"], p["pay"], round(p["rate"], 1), p["rate"],
                p["amount"],
            ], formats=agg_fmt)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
