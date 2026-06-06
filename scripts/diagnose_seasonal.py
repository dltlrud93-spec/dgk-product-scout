"""
diagnose_seasonal.py — [0단계 검증] 계절용품 시즌 캘린더 도구 타당성 진단.

구현 전 검증 전용 스크립트. 기존 동작은 일절 바꾸지 않는다(신규 파일, 기존 모듈 import 없음).
검증하려는 두 가지:
  (A) 네이버 데이터랩이 키워드의 '계절 패턴'을 실제로 보여주는가?
  (B) 그 패턴으로 키워드를 '진짜 계절상품 / 시즌가점 상시상품'으로 분류할 수 있는가?

데이터 소스: 네이버 데이터랩 '통합검색어 트렌드' API (검색광고 API 와 별개).
  엔드포인트 : POST https://openapi.naver.com/v1/datalab/search
  인증 헤더  : X-Naver-Client-Id / X-Naver-Client-Secret
  키 발급처  : https://developers.naver.com  (애플리케이션 등록 → 데이터랩(검색어트렌드) 사용 추가)
  공식 제약  : 요청당 키워드그룹 최대 5개 / 그룹당 키워드 최대 20개 / timeUnit month 지원 /
               조회 가능 시작일 2016-01-01 이후 / ratio 는 0~100 상대값(조회기간·전체그룹 통틀어
               최대 지점=100 으로 정규화. 절대 규모 아님).

[중요] 데이터랩 ratio 는 상대값이라 '규모'가 아니다(규모는 다음 단계). 이 스크립트는 검증용.
  단, 계절성 지수 = 최대월/연평균 은 스케일 불변(ratio 가 상수 c 로 정규화돼도
  max(c·x)/mean(c·x)=max(x)/mean(x) 로 약분) 이라, 상대값이라는 한계가 이 검증엔 영향 없다.

실행(프로젝트 루트에서, 데이터랩 키 필요):
    python scripts/diagnose_seasonal.py

키는 .env 의 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 에서 읽는다(검색광고용 NAVER_AD_* 와 다름).
미설정 시 발급 절차를 안내하고 종료한다(조용한 폴백 없음).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

# 윈도우 콘솔 한글 깨짐 방지(기존 diagnose_naver.py 와 동일 처리).
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# ─────────────────────────────────────────────────────────────────────────────
# 설정 (결과 보고 직접 조정하는 값들)
# ─────────────────────────────────────────────────────────────────────────────

# 분류 임계: 계절성 지수(최대월/연평균) 가 이 값 이상이면 '진짜 계절상품'.
# 결과를 보고 여기 숫자만 바꿔 재실행하면 분류가 달라진다.
SEASONALITY_THRESHOLD = 2.0

# 조회 기간: 3개 완전연도(2023~2025). 달별로 여러 해를 평균해 연도 노이즈를 평활한다.
START_DATE = "2023-01-01"
END_DATE = "2025-12-31"

# 검증 키워드(시즌 라벨 포함). 그룹당 키워드 1개로 두어 키워드별 독립 시계열을 얻는다.
SEASON_KEYWORDS: dict[str, list[str]] = {
    "겨울": ["성에제거제", "성에제거기", "워셔액", "타이어체인", "체인스프레이", "성에커버"],
    "여름": ["햇빛가리개", "차량용선풍기", "와이퍼", "발수코팅제", "송풍시트커버"],
}

# 데이터랩 API 제약/예의.
_ENDPOINT = "https://openapi.naver.com/v1/datalab/search"
_MAX_GROUPS_PER_REQUEST = 5  # 공식: 요청당 키워드그룹 최대 5개.
_RATE_LIMIT_SECONDS = 0.5    # 요청 간 간격(데이터랩 일일 쿼터 여유 있으나 예의상).
_BAR_WIDTH = 40              # 출력 막대 최대 폭.
_MONTH_LABELS = ["1월", "2월", "3월", "4월", "5월", "6월",
                 "7월", "8월", "9월", "10월", "11월", "12월"]


# ─────────────────────────────────────────────────────────────────────────────
# API 호출
# ─────────────────────────────────────────────────────────────────────────────

def _load_credentials() -> tuple[str, str]:
    """데이터랩 키를 읽는다. 없으면 발급 절차를 안내하고 종료(조용한 폴백 없음)."""
    cid = os.environ.get("NAVER_CLIENT_ID", "").strip()
    csec = os.environ.get("NAVER_CLIENT_SECRET", "").strip()
    if cid and csec:
        return cid, csec

    print("=" * 78)
    print("[중단] 데이터랩 인증키(NAVER_CLIENT_ID / NAVER_CLIENT_SECRET)가 .env 에 없습니다.")
    print("=" * 78)
    print("데이터랩 API 는 검색광고 API(NAVER_AD_*)와 '다른' 인증키를 씁니다.")
    print()
    print("발급 절차:")
    print("  1) https://developers.naver.com 로그인 → '내 애플리케이션' → '애플리케이션 등록'")
    print("  2) 사용 API 에서 '데이터랩(검색어트렌드)' 추가")
    print("  3) 발급된 Client ID / Client Secret 을 .env 에 추가:")
    print("       NAVER_CLIENT_ID=발급받은_클라이언트_ID")
    print("       NAVER_CLIENT_SECRET=발급받은_클라이언트_시크릿")
    print("  4) 다시 실행: python scripts/diagnose_seasonal.py")
    print()
    print("(검색광고 키와 달리 별도 등록이 필요하며, 일일 호출 한도는 100,000회입니다.)")
    sys.exit(1)


def _request_datalab(groups: list[dict], cid: str, csec: str) -> dict:
    """데이터랩 검색어트렌드 1회 요청(POST). 실패 시 상세 메시지와 함께 예외."""
    body = json.dumps({
        "startDate": START_DATE,
        "endDate": END_DATE,
        "timeUnit": "month",
        "keywordGroups": groups,
    }).encode("utf-8")

    req = urllib.request.Request(_ENDPOINT, data=body, method="POST")
    req.add_header("X-Naver-Client-Id", cid)
    req.add_header("X-Naver-Client-Secret", csec)
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"데이터랩 API HTTP {e.code}: {detail}\n"
            f"  (401/403 이면 키 확인, 400 이면 요청 본문/키워드 확인)"
        ) from e


def fetch_monthly_ratios(cid: str, csec: str) -> dict[str, dict[str, list]]:
    """
    모든 검증 키워드의 월별 ratio 를 수집한다.

    반환: {키워드: {"season": "겨울"|"여름", "periods": [...], "ratios": [...]}}
          periods 는 "YYYY-MM-DD"(매월 1일), ratios 는 0~100 상대값.
    """
    flat = [(kw, season) for season, kws in SEASON_KEYWORDS.items() for kw in kws]
    season_of = {kw: season for kw, season in flat}

    out: dict[str, dict[str, list]] = {}
    # 키워드 1개=그룹 1개. 요청당 최대 5그룹씩 묶어 호출.
    for i in range(0, len(flat), _MAX_GROUPS_PER_REQUEST):
        chunk = flat[i:i + _MAX_GROUPS_PER_REQUEST]
        groups = [{"groupName": kw, "keywords": [kw]} for kw, _ in chunk]
        if i > 0:
            time.sleep(_RATE_LIMIT_SECONDS)
        resp = _request_datalab(groups, cid, csec)
        for result in resp.get("results", []):
            kw = result.get("title", "")
            data = result.get("data", [])
            out[kw] = {
                "season": season_of.get(kw, "?"),
                "periods": [d["period"] for d in data],
                "ratios": [float(d["ratio"]) for d in data],
            }
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 가공: 12개월 계절 프로파일 + 지표
# ─────────────────────────────────────────────────────────────────────────────

def monthly_profile(periods: list[str], ratios: list[float]) -> list[float]:
    """
    "YYYY-MM-DD" 시계열을 달(1~12)별 평균으로 접어 12칸 프로파일을 만든다.
    데이터랩이 특정 달을 누락하면 그 달은 0 으로 둔다(검색 미미).
    """
    sums = [0.0] * 12
    counts = [0] * 12
    for period, ratio in zip(periods, ratios):
        month = int(period[5:7])  # "YYYY-MM-DD" → MM
        sums[month - 1] += ratio
        counts[month - 1] += 1
    return [(sums[m] / counts[m]) if counts[m] else 0.0 for m in range(12)]


def rising_start_month(profile: list[float], avg: float) -> int:
    """
    상승 시작월(1~12): 연중 최저월 다음부터 순환 탐색해, 처음으로 연평균을 넘어서는 달.
    계절상품이 비수기 바닥을 찍고 성수기로 진입하는 지점을 잡는다.
    """
    if avg <= 0:
        return profile.index(max(profile)) + 1
    trough = profile.index(min(profile))
    for step in range(1, 13):
        m = (trough + step) % 12
        if profile[m] >= avg:
            return m + 1
    return profile.index(max(profile)) + 1  # 전 구간 평균 이하인 비정상 케이스 방어.


def analyze(profile: list[float]) -> dict:
    """계절성 지수·정점월·상승시작월·분류를 계산한다."""
    avg = sum(profile) / len(profile) if profile else 0.0
    peak_val = max(profile) if profile else 0.0
    peak_month = profile.index(peak_val) + 1 if profile else 0
    index = (peak_val / avg) if avg > 0 else 0.0
    return {
        "avg": avg,
        "peak_val": peak_val,
        "peak_month": peak_month,
        "index": index,
        "rising_month": rising_start_month(profile, avg),
        "label": "진짜 계절상품" if index >= SEASONALITY_THRESHOLD else "시즌가점 상시상품",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 출력
# ─────────────────────────────────────────────────────────────────────────────

def _bar(value: float, scale_max: float) -> str:
    n = int(round((value / scale_max) * _BAR_WIDTH)) if scale_max > 0 else 0
    return "█" * n


def print_keyword(kw: str, rec: dict[str, list]) -> dict | None:
    """키워드 1개의 월별 프로파일과 지표를 출력하고 분석 결과를 반환."""
    print("=" * 78)
    print(f"[{rec['season']}] {kw}")
    print("=" * 78)

    if not rec["ratios"]:
        print("  데이터 없음(응답에 시계열이 비어 있음) — 검색량이 매우 적은 키워드일 수 있음.\n")
        return None

    profile = monthly_profile(rec["periods"], rec["ratios"])
    a = analyze(profile)

    # 표시용: 자기 최대월=100 으로 재정규화(스케일 불변이라 지수엔 영향 없음).
    disp_max = a["peak_val"] if a["peak_val"] > 0 else 1.0
    disp = [v / disp_max * 100 for v in profile]
    disp_avg = a["avg"] / disp_max * 100

    print(f"  최근 3년(2023~2025) 월별 상대 검색량  [정점월=100 기준, 평균선 ┃={disp_avg:.0f}]")
    for m in range(12):
        marks = []
        if m + 1 == a["peak_month"]:
            marks.append("◀ 정점")
        if m + 1 == a["rising_month"]:
            marks.append("◀ 상승시작")
        flag = "  " + " ".join(marks) if marks else ""
        print(f"    {_MONTH_LABELS[m]:>3} {disp[m]:5.0f} |{_bar(disp[m], 100):<{_BAR_WIDTH}}{flag}")

    print()
    print(f"  계절성 지수(최대월/연평균) = {a['index']:.2f}   (정점 {a['peak_month']}월, "
          f"상승 시작 {a['rising_month']}월)")
    print(f"  분류: {a['label']}  (임계 {SEASONALITY_THRESHOLD} "
          f"{'이상 →' if a['index'] >= SEASONALITY_THRESHOLD else '미만 →'} )")
    print()
    return {"keyword": kw, "season": rec["season"], **a}


def print_summary(rows: list[dict]) -> None:
    print("=" * 78)
    print("종합 요약 (계절성 지수 내림차순) — [주의] 지수는 패턴 강도일 뿐 '규모' 아님(규모는 다음 단계)")
    print("=" * 78)
    print(f"  임계 SEASONALITY_THRESHOLD = {SEASONALITY_THRESHOLD}  (파일 상단 상수로 조정)")
    print()
    print(f"  {'시즌':<4} {'키워드':<12} {'지수':>6}  {'정점':>4} {'상승':>4}  분류")
    print(f"  {'-'*4} {'-'*12} {'-'*6}  {'-'*4} {'-'*4}  {'-'*16}")
    for r in sorted(rows, key=lambda x: x["index"], reverse=True):
        print(f"  {r['season']:<4} {r['keyword']:<12} {r['index']:6.2f}  "
              f"{r['peak_month']:>3}월 {r['rising_month']:>3}월  {r['label']}")

    real = sum(1 for r in rows if r["index"] >= SEASONALITY_THRESHOLD)
    print()
    print(f"  분류 결과: 진짜 계절상품 {real}개 / 시즌가점 상시상품 {len(rows) - real}개 "
          f"(분석 가능 {len(rows)}개)")


def main() -> None:
    cid, csec = _load_credentials()
    total = sum(len(v) for v in SEASON_KEYWORDS.values())
    print(f"데이터랩 검증 시작 — 키워드 {total}개, 기간 {START_DATE}~{END_DATE}, timeUnit=month")
    print(f"임계 SEASONALITY_THRESHOLD={SEASONALITY_THRESHOLD}\n")

    data = fetch_monthly_ratios(cid, csec)

    rows: list[dict] = []
    for season, kws in SEASON_KEYWORDS.items():
        for kw in kws:
            rec = data.get(kw)
            if rec is None:
                print("=" * 78)
                print(f"[{season}] {kw}")
                print("=" * 78)
                print("  응답에 이 키워드 결과가 없음(요청/응답 매칭 실패).\n")
                continue
            r = print_keyword(kw, rec)
            if r is not None:
                rows.append(r)

    if rows:
        print_summary(rows)
    else:
        print("분석 가능한 키워드가 없습니다(모든 시계열이 비어 있음).")


if __name__ == "__main__":
    main()
