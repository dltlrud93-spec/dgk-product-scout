"""
seasonal_calendar.py — 계절 캘린더: 계절성(모양) + 규모(합산) + 안정성 통합 랭킹.

역할 분담(인증 체계 다름. 둘 다 .env 필요):
  · 계절 '모양' : 네이버 데이터랩 검색어트렌드(NAVER_CLIENT_ID/SECRET). 대표 키워드 1개의
                  월별 ratio(상대값) → 계절성 지수(최대월/연평균)·정점/상승월·연도별 안정성.
  · '규모'      : 네이버 검색광고 키워드도구(NAVER_AD_*) → Phase A 공통 코어로 'per-seed
                  기기군 합산'(src.core.search_volume.fetch_aggregated_volume).

[Phase B-1] 규모를 '단일 키워드'에서 '기기군 합산'으로 전환했다(시경 동의). 차종 성공시장
  기준선(와이퍼 ≈ 179,730 / 에어컨필터 ≈ 346,520, config 주석)과 같은 합산 척도로 통일.
[Phase B-3] 시드 목록을 전 시즌(여름/장마/겨울/봄환절기/상시대조군)으로 확장(스펙 ★초안).
[Phase B-2] 규모 임계(config.MARKET_SIZE_THRESHOLD)는 합산 재측정 후 시경이 확정 → 확정 전엔
  None(규모 게이트 보류). `측정` 모드로 전 시드 합산 규모를 벤치마크 대비 보고할 수 있다.

[설계 원칙] 계절성과 규모를 '단일 매력도 점수'로 합치지 않는다(임의 가중치 금지).
  두 축을 별도 컬럼으로 두고: 정렬 규모순(기본)/계절성순, 추천필터 = 규모≥임계 AND 계절성≥2.0.

실행(프로젝트 루트, 데이터랩 키 + 검색광고 키 필요):
    python scripts/seasonal_calendar.py            # 통합 랭킹(규모순 + 추천필터)
    python scripts/seasonal_calendar.py 계절성       # 계절성순
    python scripts/seasonal_calendar.py 전체         # 추천필터 OFF
    python scripts/seasonal_calendar.py 측정         # B-2 합산 규모 재측정만(계절성 생략) 후 정지
"""

from __future__ import annotations

import os
import sys
import time

# 프로젝트 루트 + scripts 디렉터리를 import 경로에 추가
# (config/src 와 0단계 모듈 diagnose_seasonal 을 둘 다 불러오기 위함).
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 윈도우 콘솔 한글 깨짐 방지.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import config

# 계절 '모양' 계산 함수는 0단계 모듈(diagnose_seasonal) 재사용. 0단계 파일은 수정하지 않는다.
import diagnose_seasonal as s0

# 규모 합산은 Phase A 공통 코어(계절·차종 공용). 단일 키워드 → 기기군 합산(B-1).
from src.core.search_volume import fetch_aggregated_volume

# ─────────────────────────────────────────────────────────────────────────────
# 설정 (결과 보고 직접 조정하는 값들)
# ─────────────────────────────────────────────────────────────────────────────

# 계절성 지수(최대월/연평균) 3구간 임계. 비율이라 척도 무관 → 합산 전환과 무관하게 유지.
SEASONALITY_STRONG = 2.0   # 이상 → 진짜 계절상품
SEASONALITY_WEAK = 1.8     # 이상(STRONG 미만) → 약한 계절성(경계)

# 연도별 정점월 안정성 허용오차(개월). 1 = ±1개월 흔들림까지 안정(월단위 노이즈 감안).
STABILITY_TOLERANCE_MONTHS = 1

# 추천 필터 = '규모 ≥ config.MARKET_SIZE_THRESHOLD  AND  계절성 ≥ FILTER_MIN_INDEX'.
# 단일 점수로 합치지 않는다 — 두 축의 AND 게이트. 규모 임계는 config(미확정 시 None → 보류).
FILTER_MIN_INDEX = SEASONALITY_STRONG   # 계절성 ≥ 2.0
APPLY_RECOMMENDED_FILTER = True
DEFAULT_SORT = "scale"                  # "scale"=규모순 / "seasonality"=계절성순

# 차종 성공시장 기준선(스펙 B-2 / config 주석에 기록된 기기군 합산 실측값). 임계 아님 —
# 합산 척도가 같은 자릿수로 나오는지 '검산'하는 표시 기준.
BENCHMARKS = {"와이퍼": 179_730, "에어컨필터": 346_520}

# 전 시즌 씨앗 (스펙 B-3 ★초안 — 시경 교정 전). 계절성/규모는 데이터가 판정(추측 입력 없음).
# '상시 대조군'은 상시로 정직 분류되는지 검증용. 에어컨필터는 상시대조군에 1회만(중복 제거).
SEASON_KEYWORDS: dict[str, list[str]] = {
    "여름": ["햇빛가리개", "차량용선풍기", "통풍시트커버", "차량용쿨매트", "차량용방석", "김서림방지제"],
    "장마": ["발수코팅제", "유리발수", "김서림제거제", "차량용제습제"],
    "겨울": ["부동액", "냉각수", "성에제거제", "성에제거기", "성에커버", "스노우체인",
             "타이어체인", "체인스프레이", "겨울용워셔액", "핸들커버", "열선시트커버"],
    "봄환절기": ["꽃가루", "황사"],
    "상시대조군": ["와이퍼", "워셔액", "에어컨필터", "엔진오일"],
}


# ─────────────────────────────────────────────────────────────────────────────
# 규모: Phase A 공통 코어로 키워드별 기기군 합산 (B-1)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_scale(keywords: list[str]) -> dict[str, dict]:
    """
    각 키워드를 '시드'로 보고 공통 코어로 기기군 합산 규모를 구한다(단일→합산, B-1).

    반환: {키워드: {"volume": int, "members": int}}
          volume = 소모품 멤버 합산 검색량, members = 합산에 들어간 멤버 수.
    """
    agg = fetch_aggregated_volume(keywords)
    out: dict[str, dict] = {}
    for kw in keywords:
        a = agg.get(kw, {})
        out[kw] = {
            "volume": int(a.get("total_volume", 0)),
            "members": len(a.get("member_keywords", [])),
        }
    return out


def _fmt_volume(rec: dict) -> str:
    v = rec.get("volume")
    return "확인필요" if v is None else f"{v:,}"


def _vol_num(rec: dict) -> int:
    """정렬/필터용 숫자 규모. None 은 0(필터를 자연히 못 넘김)."""
    return rec["volume"] if isinstance(rec.get("volume"), int) else 0


def _benchmark_note(vol: int) -> str:
    """벤치마크(와이퍼/에어컨필터) 대비 어디쯤인지 한 줄로."""
    return (f"{vol / BENCHMARKS['와이퍼']:.2f}×와이퍼 / "
            f"{vol / BENCHMARKS['에어컨필터']:.2f}×에어컨필터")


# ─────────────────────────────────────────────────────────────────────────────
# 계절 '모양': 데이터랩 수집 (확장된 시드용 — 0단계 _request_datalab 재사용)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_shape(season_keywords: dict[str, list[str]], cid: str, csec: str) -> dict[str, dict]:
    """
    데이터랩 검색어트렌드로 키워드별 월별 ratio 수집(계절성용). 그룹 5개/요청 배치.

    0단계 모듈의 _request_datalab/상수만 재사용하고(파일 무수정), 확장된 시드 목록을
    여기서 직접 배치 호출한다. 반환: {kw: {"season", "periods", "ratios"}}.
    """
    flat = [(kw, season) for season, kws in season_keywords.items() for kw in kws]
    season_of = {kw: s for kw, s in flat}
    out: dict[str, dict] = {}
    for i in range(0, len(flat), s0._MAX_GROUPS_PER_REQUEST):
        chunk = flat[i:i + s0._MAX_GROUPS_PER_REQUEST]
        groups = [{"groupName": kw, "keywords": [kw]} for kw, _ in chunk]
        if i > 0:
            time.sleep(s0._RATE_LIMIT_SECONDS)
        resp = s0._request_datalab(groups, cid, csec)
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
# 안정성: 연도별 정점월
# ─────────────────────────────────────────────────────────────────────────────

def per_year_peaks(periods: list[str], ratios: list[float]) -> dict[int, int]:
    """연(YYYY)별로 묶어 각 해의 정점월(1~12)을 구한다. 데이터 없는 해는 생략."""
    by_year: dict[int, list[tuple[int, float]]] = {}
    for period, ratio in zip(periods, ratios):
        year = int(period[:4])
        month = int(period[5:7])
        by_year.setdefault(year, []).append((month, ratio))
    return {year: max(pairs, key=lambda mr: mr[1])[0] for year, pairs in by_year.items()}


def _cyclic_spread(months: list[int]) -> int:
    """월 리스트의 순환(12) 최대 간격. 모든 쌍의 순환 거리 중 최댓값."""
    if len(months) < 2:
        return 0
    worst = 0
    for a in months:
        for b in months:
            d = abs(a - b)
            worst = max(worst, min(d, 12 - d))
    return worst


def stability(peaks: dict[int, int]) -> tuple[str, int]:
    """연도별 정점월의 흔들림으로 안정/불안정 판정. 반환:(라벨, 순환최대간격)."""
    spread = _cyclic_spread(list(peaks.values()))
    label = "안정" if spread <= STABILITY_TOLERANCE_MONTHS else "불안정"
    return label, spread


# ─────────────────────────────────────────────────────────────────────────────
# 분류 (3구간)
# ─────────────────────────────────────────────────────────────────────────────

def classify(index: float) -> str:
    if index >= SEASONALITY_STRONG:
        return "진짜 계절상품"
    if index >= SEASONALITY_WEAK:
        return "약한 계절성"
    return "상시상품"


# ─────────────────────────────────────────────────────────────────────────────
# 출력 — 키워드 상세
# ─────────────────────────────────────────────────────────────────────────────

def print_keyword(kw: str, season: str, rec: dict, vol_rec: dict) -> dict | None:
    """키워드 1개: 계절모양 + 합산 규모 + 연도별 정점/안정성 출력 후 종합행 데이터 반환."""
    members = vol_rec.get("members")
    extra = f"  (멤버 {members}개)" if members is not None else ""
    print("=" * 78)
    print(f"[{season}] {kw}    합산 규모(기기군) = {_fmt_volume(vol_rec)}{extra}")
    print("=" * 78)

    if not rec.get("ratios"):
        print("  데이터랩 시계열 없음 — 계절성 판정 불가(검색량 극소 가능).\n")
        return None

    profile = s0.monthly_profile(rec["periods"], rec["ratios"])  # 0단계 함수 재사용
    avg = sum(profile) / len(profile)
    peak_val = max(profile)
    peak_month = profile.index(peak_val) + 1
    index = (peak_val / avg) if avg > 0 else 0.0
    rising_month = s0.rising_start_month(profile, avg)           # 0단계 함수 재사용
    peaks = per_year_peaks(rec["periods"], rec["ratios"])
    stab_label, spread = stability(peaks)
    label = classify(index)

    disp_max = peak_val if peak_val > 0 else 1.0
    print(f"  최근 3년 월별 상대 검색량  [정점월=100, 평균선={avg / disp_max * 100:.0f}]")
    for m in range(12):
        v = profile[m] / disp_max * 100
        marks = []
        if m + 1 == peak_month:
            marks.append("◀ 정점")
        if m + 1 == rising_month:
            marks.append("◀ 상승시작")
        flag = "  " + " ".join(marks) if marks else ""
        print(f"    {s0._MONTH_LABELS[m]:>3} {v:5.0f} |"
              f"{s0._bar(v, 100):<{s0._BAR_WIDTH}}{flag}")

    yearly = "  ".join(f"{y}:{peaks[y]}월" for y in sorted(peaks))
    print()
    print(f"  계절성 지수(최대월/연평균) = {index:.2f}  →  {label}")
    print(f"  정점월 {peak_month}월 · 상승 시작 {rising_month}월")
    print(f"  연도별 정점월: {yearly}   → {stab_label}"
          f"{'' if stab_label == '안정' else f' (정점월 최대 {spread}개월 흔들림)'}")
    print()
    return {
        "keyword": kw, "season": season, "index": index, "label": label,
        "peak_month": peak_month, "rising_month": rising_month,
        "volume": vol_rec, "stability": stab_label, "spread": spread,
        "peaks": peaks,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 출력 — 통합 랭킹표
# ─────────────────────────────────────────────────────────────────────────────

def _sorted(rows: list[dict], sort_by: str) -> list[dict]:
    """정렬 축 하나로만 줄세운다(단일 점수 없음). 동률은 다른 축으로 보조 정렬."""
    if sort_by == "seasonality":
        return sorted(rows, key=lambda r: (r["index"], _vol_num(r["volume"])), reverse=True)
    return sorted(rows, key=lambda r: (_vol_num(r["volume"]), r["index"]), reverse=True)


def _print_table(rows: list[dict]) -> None:
    """컬럼: 제품 | 시즌 | 계절성지수 | 정점/상승월 | 절대규모(합산) | 안정성."""
    print(f"  {'제품':<12} {'시즌':<8} {'계절성지수':>7} {'정점/상승':>9} "
          f"{'절대규모(합산)':>13} {'안정성':<8}")
    print(f"  {'-'*12} {'-'*8} {'-'*7} {'-'*9} {'-'*13} {'-'*8}")
    for r in rows:
        pr = f"{r['peak_month']}월/{r['rising_month']}월"
        print(f"  {r['keyword']:<12} {r['season']:<8} {r['index']:>7.2f} {pr:>9} "
              f"{_fmt_volume(r['volume']):>13} {r['stability']:<8}")


def print_summary(rows: list[dict], sort_by: str, apply_filter: bool) -> None:
    sort_label = "계절성순" if sort_by == "seasonality" else "규모순"
    thr = config.MARKET_SIZE_THRESHOLD
    threshold_known = isinstance(thr, (int, float)) and not isinstance(thr, bool)
    # 임계 미확정이면 규모 게이트를 적용할 수 없다 → 추천필터 보류(전체 표시).
    effective_filter = apply_filter and threshold_known

    print("=" * 78)
    print("통합 랭킹 — 전 시즌 후보 한 표  (단일 점수 없음: 규모·계절성은 별도 축)")
    print("=" * 78)
    thr_text = f"{thr:,}" if threshold_known else "[확인 필요 — 미확정]"
    print(f"  정렬: {sort_label}  |  추천필터(규모 ≥ {thr_text} AND 계절성 ≥ {FILTER_MIN_INDEX}): "
          f"{'ON' if effective_filter else 'OFF'}")
    if apply_filter and not threshold_known:
        print("  [확인 필요] config.MARKET_SIZE_THRESHOLD 미확정 → 규모 필터 보류, 전체를 "
              "정렬만 해서 표시(B-2 재측정→시경 확정 후 활성).")
    print(f"  [전환] 정렬 '규모'/'계절성' · 필터 끄기 '전체'\n")

    def passes(r: dict) -> bool:
        return _vol_num(r["volume"]) >= thr and r["index"] >= FILTER_MIN_INDEX

    kept = [r for r in rows if (not effective_filter) or passes(r)]
    dropped = [r for r in rows if effective_filter and not passes(r)]

    if kept:
        _print_table(_sorted(kept, sort_by))
    else:
        print("  (추천 후보 없음 — 규모·계절성 둘 다 만족하는 제품이 없음)")
    label = "추천 후보" if effective_filter else "표시"
    print(f"\n  → {label} {len(kept)}개 / 전체 분석 {len(rows)}개")

    if dropped:
        print(f"\n  ── 필터 제외 (사유: 규모<{thr:,} / 계절성<{FILTER_MIN_INDEX} / 둘다) ──")
        for r in _sorted(dropped, sort_by):
            why = []
            if _vol_num(r["volume"]) < thr:
                why.append("규모")
            if r["index"] < FILTER_MIN_INDEX:
                why.append("계절성")
            print(f"    {r['keyword']:<12} {r['season']:<8} 지수 {r['index']:>5.2f}  "
                  f"규모 {_fmt_volume(r['volume']):>10}  ← {'+'.join(why)} 미달")

    print("\n  [주의] 규모는 기기군 합산(B-1). 발주 데드라인 미포함. "
          "임계 확정 전이면 규모 필터는 보류 상태.")


# ─────────────────────────────────────────────────────────────────────────────
# B-2 측정 모드: 전 시드 합산 규모 재측정 + 벤치마크 대비 (계절성 생략, 정지)
# ─────────────────────────────────────────────────────────────────────────────

def run_measurement(season_keywords: dict[str, list[str]]) -> None:
    flat = [(kw, season) for season, kws in season_keywords.items() for kw in kws]
    keywords = [kw for kw, _ in flat]
    season_of = {kw: s for kw, s in flat}

    print("=" * 78)
    print(f"[B-2] 합산 규모 재측정 — 시드 {len(keywords)}개 (검색광고 키워드도구, 시드당 1회 호출)")
    print("=" * 78)
    print(f"  벤치마크(검산 기준): 와이퍼 ≈ {BENCHMARKS['와이퍼']:,} / "
          f"에어컨필터 ≈ {BENCHMARKS['에어컨필터']:,}")
    print(f"  규모 임계 MARKET_SIZE_THRESHOLD = {config.MARKET_SIZE_THRESHOLD}  "
          f"(확정 전 — 재측정 보고용)\n")

    scale = fetch_scale(keywords)
    rows = [{"keyword": kw, "season": season_of[kw], **scale[kw]} for kw in keywords]
    rows.sort(key=lambda r: r["volume"], reverse=True)

    print(f"  {'제품':<12} {'시즌':<8} {'합산규모':>10} {'멤버수':>5}  벤치마크 대비")
    print(f"  {'-'*12} {'-'*8} {'-'*10} {'-'*5}  {'-'*30}")
    for r in rows:
        print(f"  {r['keyword']:<12} {r['season']:<8} {r['volume']:>10,} "
              f"{r['members']:>5}  {_benchmark_note(r['volume'])}")

    print("\n  [정지] 임계 확정·씨앗 교정 대기. (B-4/B-5/B-6 는 확정 후 진행)")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args(argv: list[str]) -> tuple[str, bool, bool]:
    """반환:(sort_by, apply_filter, measure). 미지정은 기본값."""
    sort_by = DEFAULT_SORT
    apply_filter = APPLY_RECOMMENDED_FILTER
    measure = False
    for a in argv:
        t = a.strip().lower()
        if t in ("계절성", "seasonality", "--sort=seasonality"):
            sort_by = "seasonality"
        elif t in ("규모", "scale", "--sort=scale"):
            sort_by = "scale"
        elif t in ("전체", "all", "--all", "--no-filter"):
            apply_filter = False
        elif t in ("측정", "measure", "--measure"):
            measure = True
    return sort_by, apply_filter, measure


def main() -> None:
    sort_by, apply_filter, measure = _parse_args(sys.argv[1:])

    if measure:
        run_measurement(SEASON_KEYWORDS)   # B-2: 합산 규모만 재측정 후 정지(계절성 호출 안 함).
        return

    cid, csec = s0._load_credentials()       # 데이터랩 키 검증
    flat = [(kw, season) for season, kws in SEASON_KEYWORDS.items() for kw in kws]
    keywords = [kw for kw, _ in flat]
    season_of = {kw: season for kw, season in flat}

    print(f"계절 캘린더 — 시드 {len(keywords)}개, 기간 {s0.START_DATE}~{s0.END_DATE}")
    print(f"임계 STRONG={SEASONALITY_STRONG} WEAK={SEASONALITY_WEAK} "
          f"안정성허용={STABILITY_TOLERANCE_MONTHS}개월\n")

    print("데이터랩에서 계절 모양 수집 중...")
    shape = fetch_shape(SEASON_KEYWORDS, cid, csec)        # 데이터랩(계절 모양)
    print("검색광고 키워드도구에서 합산 규모 수집 중...\n")
    volumes = fetch_scale(keywords)                        # 공통 코어(규모 합산)

    rows: list[dict] = []
    for kw in keywords:
        rec = shape.get(kw, {})
        r = print_keyword(kw, season_of[kw], rec, volumes.get(kw, {"volume": None, "members": None}))
        if r is not None:
            rows.append(r)

    if rows:
        print_summary(rows, sort_by, apply_filter)
    else:
        print("분석 가능한 키워드가 없습니다(계절 시계열이 모두 비어 있음).")


if __name__ == "__main__":
    main()
