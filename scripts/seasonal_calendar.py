"""
seasonal_calendar.py — [1단계] 계절성(모양) + 규모(절대 검색량) + 안정성 결합.

0단계(scripts/diagnose_seasonal.py)에서 데이터랩이 계절 패턴을 실제로 보여주고
키워드를 계절성으로 분류 가능함을 검증했다. 1단계는 거기에 '규모'와 '안정성'을 붙인다.

두 API 를 결합한다(인증 체계가 서로 다름. 둘 다 .env 에 있어야 함):
  · 계절 '모양' : 네이버 데이터랩 검색어트렌드(NAVER_CLIENT_ID/SECRET) — 0단계 모듈 재사용.
                  ratio 는 상대값이므로 '지수/정점월/상승월/연도별 안정성'만 여기서 뽑는다.
  · 절대 '규모' : 네이버 검색광고 키워드도구(NAVER_AD_*) — 기존 NaverAdapter 재사용.
                  키워드별 monthlyPcQcCnt+monthlyMobileQcCnt = 절대 월검색량.

출력:
  · 키워드별 상세(월별 막대 + 지수/정점/상승/연도별 정점/안정성) — 증거.
  · 그리고 '통합 랭킹표': 겨울·여름 후보를 한 표에. 시즌별로 쪼개지 않는다.
    컬럼 = 제품 | 시즌 | 계절성지수 | 정점/상승월 | 절대규모(월검색) | 안정성.

[설계 원칙] 계절성과 규모를 '단일 매력도 점수'로 합치지 않는다(임의 가중치 금지).
  대신 두 축을 그대로 두고:
    · 정렬 선택 : 규모순(기본) / 계절성순.
    · 추천 필터 : '규모 ≥ 20000  AND  계절성 ≥ 2.0' (AND 게이트, 기본 ON, 끌 수 있음).
      → 차량용선풍기·햇빛가리개처럼 '둘 다' 갖춘 후보가 통과, 성에류(규모 수백)는 제외.
  분류 3구간(지수 ≥2.0 진짜계절 / 1.8~2.0 약한계절 / <1.8 상시)은 상세 블록에서 표기.

[중요] 발주 데드라인 미포함(다음 단계). 규모는 키워드 '개별' 월검색량(겨울 부동액·냉각수
  시즌코어 합산 ~5만 반영은 보류 — 다음 단계). 여기까지는 계절성+규모+안정성.

실행(프로젝트 루트, 데이터랩 키 + 검색광고 키 둘 다 필요):
    python scripts/seasonal_calendar.py                 # 기본: 규모순 + 추천필터 ON
    python scripts/seasonal_calendar.py 계절성           # 계절성순 정렬
    python scripts/seasonal_calendar.py 전체             # 추천필터 OFF(전체 표시)
    python scripts/seasonal_calendar.py 계절성 전체       # 조합 가능
"""

from __future__ import annotations

import os
import sys

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

# --- 0단계 모듈 재사용(계절 모양). 0단계 파일은 일절 수정하지 않는다. ---
import diagnose_seasonal as s0

# --- 기존 검색광고 어댑터 재사용(절대 규모). 재발명/중복 서명 로직 없음. ---
from src.adapters.csv_adapter import _parse_volume
from src.adapters.naver_adapter import (
    _FIELD_MONTHLY_MOBILE,
    _FIELD_MONTHLY_PC,
    _FIELD_REL_KEYWORD,
    NaverAdapter,
)

# ─────────────────────────────────────────────────────────────────────────────
# 설정 (결과 보고 직접 조정하는 값들)
# ─────────────────────────────────────────────────────────────────────────────

# 계절성 지수(최대월/연평균) 3구간 임계.
SEASONALITY_STRONG = 2.0   # 이상 → 진짜 계절상품
SEASONALITY_WEAK = 1.8     # 이상(STRONG 미만) → 약한 계절성(경계)

# 연도별 정점월 안정성 허용오차(개월). 0 이면 3년 정점월이 '완전히 같아야' 안정.
# 1 로 올리면 ±1개월 흔들림까지 안정으로 본다(데이터랩 월단위 노이즈 감안 시).
STABILITY_TOLERANCE_MONTHS = 1

# 추천 필터: '규모 ≥ FILTER_MIN_VOLUME 그리고 계절성 ≥ FILTER_MIN_INDEX' 둘 다 충족만 통과.
# 계절성+규모를 단일 점수로 합치지 않는다(임의 가중치 금지). 두 축을 'AND 게이트'로만 쓴다.
FILTER_MIN_VOLUME = 20_000   # 월검색량(절대) 하한
FILTER_MIN_INDEX = 2.0       # 계절성 지수 하한(= 진짜 계절상품 구간)
APPLY_RECOMMENDED_FILTER = True   # 기본 ON. CLI '전체'/'all'/'--no-filter' 로 끌 수 있음.

# 정렬 축(단일 점수 없음 — 둘 중 하나로만 줄세움). CLI '규모'/'계절성' 로 선택.
DEFAULT_SORT = "scale"       # "scale"=규모순 / "seasonality"=계절성순

_KEYWORDS_PER_AD_CALL = 5  # 키워드도구 호출당 hint 최대 5개(공식).


# ─────────────────────────────────────────────────────────────────────────────
# 규모: 검색광고 키워드도구에서 키워드별 절대 월검색량
# ─────────────────────────────────────────────────────────────────────────────

def _norm(kw: str) -> str:
    """매칭용 정규화: 공백 제거(키워드도구 응답은 공백 없이 오는 경우가 있음)."""
    return "".join(str(kw).split())


def fetch_absolute_volumes(keywords: list[str]) -> dict[str, dict]:
    """
    각 키워드의 절대 월검색량(PC+모바일)을 키워드도구에서 가져온다.

    응답 keywordList 에서 relKeyword 가 '정확히 일치'하는 행만 그 키워드 값으로 쓴다
    (연관어가 아니라 그 키워드 자체). exact-match 라 hint 5개 배치해도 섞이지 않는다.

    반환: {키워드: {"volume": int|None, "low": bool}}
          volume None = 응답에 해당 키워드 행이 없음(검색량 극소 등). low=True 는 '< 10' 표기.
    """
    adapter = NaverAdapter(keywords)  # 생성자가 NAVER_AD_* 키 검증(없으면 명시 예외).
    by_norm: dict[str, dict] = {}
    for i in range(0, len(keywords), _KEYWORDS_PER_AD_CALL):
        chunk = keywords[i:i + _KEYWORDS_PER_AD_CALL]
        if i > 0:
            adapter._sleep(adapter.rate_limit_seconds)  # 호출 간 rate limit
        for row in adapter._request_keywordstool(chunk):
            rel = str(row.get(_FIELD_REL_KEYWORD) or "").strip()
            if rel:
                by_norm.setdefault(_norm(rel), row)  # 첫 출현만(중복 방지)

    out: dict[str, dict] = {}
    for kw in keywords:
        row = by_norm.get(_norm(kw))
        if row is None:
            out[kw] = {"volume": None, "low": False}
            continue
        raw_pc = row.get(_FIELD_MONTHLY_PC)
        raw_mo = row.get(_FIELD_MONTHLY_MOBILE)
        vol = int(_parse_volume(raw_pc) + _parse_volume(raw_mo))
        # '< 10' 처럼 0 으로 파싱된 저소량 표기 감지(0 과 구분해 보여주기 위함).
        low = vol == 0 and any(
            str(v).strip().startswith("<") for v in (raw_pc, raw_mo)
        )
        out[kw] = {"volume": vol, "low": low}
    return out


def _fmt_volume(rec: dict) -> str:
    if rec["volume"] is None:
        return "확인필요"   # 응답에 키워드 행 없음
    if rec["low"]:
        return "<10"
    return f"{rec['volume']:,}"


def _vol_num(rec: dict) -> int:
    """정렬/필터용 숫자 규모. 확인필요(None)·'<10' 은 0 으로(필터를 자연히 못 넘김)."""
    return rec["volume"] if isinstance(rec.get("volume"), int) else 0


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
    peaks: dict[int, int] = {}
    for year, pairs in by_year.items():
        peaks[year] = max(pairs, key=lambda mr: mr[1])[0]
    return peaks


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
    months = list(peaks.values())
    spread = _cyclic_spread(months)
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
# 출력
# ─────────────────────────────────────────────────────────────────────────────

def print_keyword(kw: str, season: str, rec: dict, vol_rec: dict) -> dict | None:
    """키워드 1개: 계절모양 + 규모 + 연도별 정점/안정성 출력 후 종합행 데이터 반환."""
    print("=" * 78)
    print(f"[{season}] {kw}    절대 월검색량(규모) = {_fmt_volume(vol_rec)}")
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

    # 12개월 막대(자기 정점월=100 재정규화 — 스케일 불변이라 지수엔 영향 없음).
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


def _sorted(rows: list[dict], sort_by: str) -> list[dict]:
    """정렬 축 하나로만 줄세운다(단일 점수 없음). 동률은 다른 축으로 보조 정렬."""
    if sort_by == "seasonality":
        return sorted(rows, key=lambda r: (r["index"], _vol_num(r["volume"])), reverse=True)
    return sorted(rows, key=lambda r: (_vol_num(r["volume"]), r["index"]), reverse=True)


def _print_table(rows: list[dict]) -> None:
    """요청 컬럼만: 제품 | 시즌 | 계절성지수 | 정점/상승월 | 절대규모(월검색) | 안정성."""
    print(f"  {'제품':<12} {'시즌':<4} {'계절성지수':>7} {'정점/상승':>9} "
          f"{'절대규모(월검색)':>14} {'안정성':<8}")
    print(f"  {'-'*12} {'-'*4} {'-'*7} {'-'*9} {'-'*14} {'-'*8}")
    for r in rows:
        pr = f"{r['peak_month']}월/{r['rising_month']}월"
        print(f"  {r['keyword']:<12} {r['season']:<4} {r['index']:>7.2f} {pr:>9} "
              f"{_fmt_volume(r['volume']):>14} {r['stability']:<8}")


def print_summary(rows: list[dict], sort_by: str, apply_filter: bool) -> None:
    sort_label = "계절성순" if sort_by == "seasonality" else "규모순"
    print("=" * 78)
    print("통합 랭킹 — 겨울·여름 후보 한 표  (단일 점수 없음: 규모·계절성은 별도 축)")
    print("=" * 78)
    print(f"  정렬: {sort_label}  |  추천필터(규모 ≥ {FILTER_MIN_VOLUME:,} "
          f"AND 계절성 ≥ {FILTER_MIN_INDEX}): {'ON' if apply_filter else 'OFF'}")
    print(f"  [전환] 정렬 '규모'/'계절성' · 필터 끄기 '전체'  (예: python scripts/"
          f"seasonal_calendar.py 계절성 전체)\n")

    def passes(r: dict) -> bool:
        return _vol_num(r["volume"]) >= FILTER_MIN_VOLUME and r["index"] >= FILTER_MIN_INDEX

    kept = [r for r in rows if (not apply_filter) or passes(r)]
    dropped = [r for r in rows if apply_filter and not passes(r)]

    if kept:
        _print_table(_sorted(kept, sort_by))
    else:
        print("  (추천 후보 없음 — 규모·계절성 둘 다 만족하는 제품이 없음)")
    print(f"\n  → 추천 후보 {len(kept)}개 / 전체 분석 {len(rows)}개")

    # 필터 ON 일 때만, 무엇이 왜 빠졌는지 따로 보여준다(임의 점수 없이 탈락사유 명시).
    if dropped:
        print("\n  ── 필터 제외 (사유: 규모<2만 / 계절성<2.0 / 둘다) ──")
        for r in _sorted(dropped, sort_by):
            v = _vol_num(r["volume"])
            why = []
            if v < FILTER_MIN_VOLUME:
                why.append("규모")
            if r["index"] < FILTER_MIN_INDEX:
                why.append("계절성")
            print(f"    {r['keyword']:<12} {r['season']:<4} 지수 {r['index']:>5.2f}  "
                  f"규모 {_fmt_volume(r['volume']):>10}  ← {'+'.join(why)} 미달")

    print("\n  [주의] 규모는 키워드 '개별' 절대 월검색량(기기군 합산 아님). "
          "겨울 부동액·냉각수 시즌코어 합산 반영은 다음 단계. 발주 데드라인 미포함.")


def _parse_args(argv: list[str]) -> tuple[str, bool]:
    """CLI 옵션 파싱(단순 토큰). 반환:(sort_by, apply_filter). 미지정은 기본값."""
    sort_by = DEFAULT_SORT
    apply_filter = APPLY_RECOMMENDED_FILTER
    for a in argv:
        t = a.strip().lower()
        if t in ("계절성", "seasonality", "--sort=seasonality"):
            sort_by = "seasonality"
        elif t in ("규모", "scale", "--sort=scale"):
            sort_by = "scale"
        elif t in ("전체", "all", "--all", "--no-filter"):
            apply_filter = False
    return sort_by, apply_filter


def main() -> None:
    sort_by, apply_filter = _parse_args(sys.argv[1:])

    # 1) 데이터랩 키 검증 + 계절 모양 수집(0단계 모듈 그대로).
    cid, csec = s0._load_credentials()
    flat = [(kw, season) for season, kws in s0.SEASON_KEYWORDS.items() for kw in kws]
    keywords = [kw for kw, _ in flat]
    season_of = {kw: season for kw, season in flat}

    print(f"1단계 결합 시작 — 키워드 {len(keywords)}개, "
          f"기간 {s0.START_DATE}~{s0.END_DATE}")
    print(f"임계 STRONG={SEASONALITY_STRONG} WEAK={SEASONALITY_WEAK} "
          f"안정성허용={STABILITY_TOLERANCE_MONTHS}개월\n")

    shape = s0.fetch_monthly_ratios(cid, csec)          # 데이터랩(계절 모양)

    # 2) 절대 규모 수집(검색광고 키워드도구). 키 없으면 여기서 명시 예외.
    print("검색광고 키워드도구에서 절대 월검색량 수집 중...\n")
    volumes = fetch_absolute_volumes(keywords)          # 검색광고(규모)

    # 3) 키워드별 결합 출력.
    rows: list[dict] = []
    for kw in keywords:
        rec = shape.get(kw, {})
        r = print_keyword(kw, season_of[kw], rec, volumes.get(kw, {"volume": None, "low": False}))
        if r is not None:
            rows.append(r)

    if rows:
        print_summary(rows, sort_by, apply_filter)
    else:
        print("분석 가능한 키워드가 없습니다(계절 시계열이 모두 비어 있음).")


if __name__ == "__main__":
    main()
