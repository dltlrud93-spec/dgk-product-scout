"""
test_campaign_analytics.py — 스마트스토어 마케팅분석 표 파싱·집계 검증(순수 함수).
"""

from __future__ import annotations

from io import BytesIO

from openpyxl import load_workbook

from src.core.campaign_analytics import (
    _is_num,
    _num,
    build_analytics_xlsx,
    parse_smartstore_table,
)


def _row(channel, medium, detail, keyword, inflow, pay, amount):
    """12컬럼 데이터행 생성(검증된 인덱스: 0/2/3/4/6/9/11)."""
    cols = ["-"] * 12
    cols[0] = channel
    cols[2] = medium
    cols[3] = detail
    cols[4] = keyword
    cols[6] = inflow
    cols[9] = pay
    cols[11] = amount
    return "\t".join(cols)


# 헤더 2줄(채널그룹 등 — col0 가 채널값이 아니라 자동 skip).
_HEADER = "\t".join(["채널그룹", "채널", "nt_medium", "nt_detail", "nt_keyword",
                     "유입(전체)", "유입수", "c7", "c8", "결제수", "c10", "결제금액"])
_HEADER2 = "\t".join(["속성"] + ["-"] * 11)

# 시경 표 구조 재현(전체 그랜드토탈 + 모바일/PC 기기행, 매체 대소문자 혼용·빈 keyword·형식 혼용).
_SAMPLE = "\n".join([
    _HEADER, _HEADER2,
    _row("전체", "", "", "", "1,585", "113", "2,919,930"),          # 그랜드토탈
    _row("모바일", "blog", "P01filter2ea", "에어컨필터", "150", "5", "100,000"),
    _row("PC", "blog", "P01filter2ea", "에어컨필터", "79", "4", "80,000"),
    _row("모바일", "blog", "260622_와이퍼_렉스턴스포츠", "와이퍼", "76", "19", "300,000"),
    _row("모바일", "REVU", "P02wiper", "", "200", "10", "250,000"),  # keyword 빈칸
    _row("PC", "revu", "P02wiper", "-", "86", "4", "90,000"),        # keyword "-" + 대소문자 혼용
    "깨진\t줄",                                                       # 컬럼 부족 → skip
])


def test_summary_from_total_row():
    out = parse_smartstore_table(_SAMPLE)
    s = out["summary"]
    assert s["inflow"] == 1585
    assert s["pay"] == 113
    assert round(s["pay_rate"], 1) == 7.1
    assert s["amount"] == 2919930


def test_by_medium_case_insensitive_merge():
    out = parse_smartstore_table(_SAMPLE)
    by = {m["medium"]: m for m in out["by_medium"]}
    assert by["blog"]["inflow"] == 305 and by["blog"]["pay"] == 28
    assert by["revu"]["inflow"] == 286 and by["revu"]["pay"] == 14   # REVU+revu 통합
    # rate 내림차순 정렬
    rates = [m["rate"] for m in out["by_medium"]]
    assert rates == sorted(rates, reverse=True)


def test_by_campaign_merges_mobile_and_pc():
    out = parse_smartstore_table(_SAMPLE)
    camp = {c["campaign"]: c for c in out["by_campaign"]}
    assert camp["P01filter2ea"]["inflow"] == 229   # 150(모바일)+79(PC)
    assert camp["P01filter2ea"]["pay"] == 9
    rates = [c["rate"] for c in out["by_campaign"]]
    assert rates == sorted(rates, reverse=True)


def test_warnings_three_kinds_detected():
    out = parse_smartstore_table(_SAMPLE)
    w = " | ".join(out["warnings"])
    assert "대소문자" in w        # REVU/revu
    assert "비어있음" in w        # keyword "" / "-"
    assert "형식" in w            # 언더스코어형 + 비언더스코어형 혼용
    assert len(out["warnings"]) >= 3


def test_by_product_new_format_token():
    """신규형 detail 만 있는 표 → 제품=토큰[1] 로 by_product 집계."""
    raw = "\n".join([
        _HEADER,
        _row("전체", "", "", "", "76", "19", "300,000"),
        _row("모바일", "blog", "260622_와이퍼_렉스턴스포츠", "와이퍼", "76", "19", "300,000"),
    ])
    out = parse_smartstore_table(raw)
    prod = {p["product"]: p for p in out["by_product"]}
    assert "와이퍼" in prod
    assert prod["와이퍼"]["inflow"] == 76 and prod["와이퍼"]["pay"] == 19


def test_by_product_empty_when_no_new_format():
    """신규형 detail 이 하나도 없으면 by_product 는 빈 리스트."""
    raw = "\n".join([
        _HEADER,
        _row("모바일", "blog", "P01filter2ea", "에어컨필터", "150", "5", "100,000"),
    ])
    out = parse_smartstore_table(raw)
    assert out["by_product"] == []


def test_summary_falls_back_to_sum_when_no_total_row():
    """'전체' 행이 없으면 기기(모바일/PC)행 합산으로 summary."""
    raw = "\n".join([
        _HEADER,
        _row("모바일", "blog", "P01filter2ea", "에어컨필터", "150", "5", "100,000"),
        _row("PC", "blog", "P01filter2ea", "에어컨필터", "79", "4", "80,000"),
    ])
    out = parse_smartstore_table(raw)
    assert out["summary"]["inflow"] == 229 and out["summary"]["pay"] == 9


def test_num_parsing_helpers():
    assert _num("1,585") == 1585.0
    assert _num("7.13%") == 7.13
    assert _num("") == 0.0
    assert _num("abc") == 0.0
    assert _is_num("1,585") is True
    assert _is_num("") is False
    assert _is_num("-") is False


def test_broken_and_short_lines_skipped():
    raw = "\n".join([
        _HEADER,
        "짧은\t줄",                                   # 컬럼 부족
        "그냥아무텍스트탭없음",                         # 탭 없음
        _row("기타", "blog", "X", "kw", "10", "1", "1,000"),  # col0 채널 아님 → skip
        _row("모바일", "blog", "P01filter2ea", "에어컨필터", "150", "5", "100,000"),
    ])
    out = parse_smartstore_table(raw)
    # 유효 기기행 1개만 집계됨
    assert out["summary"]["inflow"] == 150
    assert len(out["by_campaign"]) == 1


def test_rate_zero_division_guard():
    raw = "\n".join([
        _HEADER,
        _row("전체", "", "", "", "0", "0", "0"),
    ])
    out = parse_smartstore_table(raw)
    assert out["summary"]["pay_rate"] == 0.0


# ── rows 키(엑셀 시트1 재현용) ──────────────────────────────────────────────
def test_rows_includes_total_and_device_with_source():
    # source(col1) 가 채워진 표 — _row 헬퍼는 col1 을 "-" 로 두므로 직접 구성.
    def _r(channel, source, medium, detail, kw, inflow, pay, amount):
        cols = ["-"] * 12
        cols[0], cols[1], cols[2], cols[3], cols[4] = channel, source, medium, detail, kw
        cols[6], cols[9], cols[11] = inflow, pay, amount
        return "\t".join(cols)

    raw = "\n".join([
        _HEADER,
        _r("전체", "naver.blog", "", "", "", "1,585", "113", "2,919,930"),
        _r("모바일", "naver.blog", "blog", "P01filter2ea", "에어컨필터", "150", "5", "100,000"),
    ])
    out = parse_smartstore_table(raw)
    rows = out["rows"]
    assert len(rows) == 2                       # 전체 + 기기행 모두 포함
    assert rows[0]["channel"] == "전체" and rows[0]["inflow"] == 1585
    assert rows[0]["source"] == "naver.blog"    # col1 채워짐
    assert round(rows[1]["pay_rate"], 4) == round(5 / 150 * 100, 4)


# ── 엑셀 내보내기 ────────────────────────────────────────────────────────────
def test_build_analytics_xlsx_two_sheets_and_numbers():
    result = parse_smartstore_table(_SAMPLE)
    data = build_analytics_xlsx(result)
    assert data and len(data) > 0

    wb = load_workbook(BytesIO(data))
    assert wb.sheetnames == ["원본붙여넣기", "분석결과"]

    # 시트1: 전체행 유입 1585 가 어딘가 존재
    ws1 = wb["원본붙여넣기"]
    vals1 = [c.value for row in ws1.iter_rows() for c in row]
    assert 1585 in vals1

    # 시트2: 요약 항목/값 매핑
    ws2 = wb["분석결과"]
    summary = {}
    for row in ws2.iter_rows(values_only=True):
        if row and row[0] in ("총 유입", "총 결제", "결제율_표시", "결제율_정확", "결제금액"):
            summary[row[0]] = row[1]
    assert summary["총 유입"] == 1585
    assert summary["총 결제"] == 113
    assert summary["결제금액"] == 2919930
    assert summary["결제율_표시"] == 7.1
    assert round(summary["결제율_정확"], 4) == 7.1293   # 113/1585*100 풀소수
