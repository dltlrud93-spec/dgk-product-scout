"""
seasonal_calendar.py — 계절 캘린더: 계절성(모양) + 규모(단일 키워드) + 안정성 통합 랭킹.

역할 분담(인증 체계 다름. 둘 다 .env 필요):
  · 계절 '모양' : 네이버 데이터랩 검색어트렌드(NAVER_CLIENT_ID/SECRET). 대표 키워드 1개의
                  월별 ratio(상대값) → 계절성 지수(최대월/연평균)·정점/상승월·연도별 안정성.
  · '규모'      : 네이버 검색광고 키워드도구(NAVER_AD_*). 그 키워드 '단일'의
                  monthlyPcQcCnt + monthlyMobileQcCnt (연관어 합산 아님).

[규모 척도 = 단일 키워드] Phase B-1(공통 코어 기기군 합산)은 되돌렸다. 합산은 시드별로
  드리프트 편차가 커서(와이퍼는 벤치마크 적중하나 햇빛가리개·냉각수·성에제거기 등은 가정용
  선풍기·에어컨청소·칫솔살균기 같은 타도메인을 끌어와 10배 이상 과대) 계절 캘린더의 척도로
  부적합했다. 관련성 가드는 블랙리스트가 무한확장돼 채택하지 않는다. 대신 단일 키워드로 통일하고,
  드리프트가 컸던 씨앗은 '차량특정 형태'(예: 햇빛가리개 vs 차량용햇빛가리개)를 나란히 측정해
  시경의 씨앗 확정을 돕는다.
  ※ 합산 코어(src.core.search_volume)는 정확하며 Phase C(차종 스캐너)가 쓴다 — 건드리지 않음.
  ※ 겨울 시즌코어 합산(B-4)은 보류 — 단일 척도에 합산값을 섞으면 척도 충돌.

[설계 원칙] 계절성과 규모를 '단일 매력도 점수'로 합치지 않는다(임의 가중치 금지).
  두 축을 별도 컬럼으로: 정렬 규모순(기본)/계절성순, 추천필터 = 규모≥임계 AND 계절성≥2.0.

실행(프로젝트 루트, 데이터랩 키 + 검색광고 키 필요):
    python scripts/seasonal_calendar.py            # 통합 랭킹(규모순 + 추천필터)
    python scripts/seasonal_calendar.py 계절성       # 계절성순
    python scripts/seasonal_calendar.py 전체         # 추천필터 OFF
    python scripts/seasonal_calendar.py 측정         # 씨앗 재측정(단일검색량 + ≥임계 + 계절성 + 차량변형)
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

# 검색광고 호출 경로/응답 필드명 재사용(단일 키워드 검색량용). 합산 코어는 쓰지 않는다.
from src.adapters.csv_adapter import _parse_volume
from src.adapters.naver_adapter import NaverAdapter
from src.core.search_volume import (
    FIELD_MONTHLY_MOBILE,
    FIELD_MONTHLY_PC,
    FIELD_REL_KEYWORD,
)

# ─────────────────────────────────────────────────────────────────────────────
# 설정 (결과 보고 직접 조정하는 값들)
# ─────────────────────────────────────────────────────────────────────────────

# 계절성 지수(최대월/연평균) 3구간 임계. 비율이라 척도 무관 → 유지.
SEASONALITY_STRONG = 2.0   # 이상 → 진짜 계절상품
SEASONALITY_WEAK = 1.8     # 이상(STRONG 미만) → 약한 계절성(경계)

# 연도별 정점월 안정성 허용오차(개월). 1 = ±1개월 흔들림까지 안정.
STABILITY_TOLERANCE_MONTHS = 1

# 추천 필터 = '규모 ≥ config.MARKET_SIZE_THRESHOLD  AND  계절성 ≥ FILTER_MIN_INDEX'.
FILTER_MIN_INDEX = SEASONALITY_STRONG   # 계절성 ≥ 2.0
APPLY_RECOMMENDED_FILTER = True
DEFAULT_SORT = "scale"                  # "scale"=규모순 / "seasonality"=계절성순

# 단일 키워드 척도의 '원래' 참고 임계(B-1 이전 값). 측정 표의 ≥ 표시에만 쓰는 참고선.
# 최종 임계는 시경이 확정해 config.MARKET_SIZE_THRESHOLD 에 입력(그 전엔 규모 필터 보류).
SINGLE_SCALE_THRESHOLD_REF = 20_000

_KEYWORDS_PER_AD_CALL = 5  # 키워드도구 호출당 hint 최대 5개(공식).

# 전 시즌 씨앗 (시경 확정). 규칙: 기본형 vs 차량특정형 중 검색량 큰 쪽.
#   · 에어컨필터 → 자동차에어컨필터(21,960 > 19,200), 햇빛가리개 → 차량용햇빛가리개(20,820, 가정용 드리프트 제거)
#   · 부동액·냉각수·발수코팅제: 기본형이 차량 수요 대부분 → 기본형 유지
#   · 꽃가루: 제거(제품 아닌 봄 현상어, 수요는 에어컨필터로 흡수 — 중복계상 방지)
# '상시 대조군'은 상시로 정직 분류되는지 검증용.
SEASON_KEYWORDS: dict[str, list[str]] = {
    "여름": ["차량용햇빛가리개", "차량용선풍기", "통풍시트커버", "차량용쿨매트", "차량용방석", "김서림방지제"],
    "장마": ["발수코팅제", "유리발수", "김서림제거제", "차량용제습제"],
    "겨울": ["부동액", "냉각수", "성에제거제", "성에제거기", "성에커버", "스노우체인",
             "타이어체인", "체인스프레이", "겨울용워셔액", "핸들커버", "열선시트커버"],
    "봄환절기": ["황사"],
    "상시대조군": ["와이퍼", "워셔액", "자동차에어컨필터", "엔진오일"],
}

# 겨울 케어 묶음(B-6 #6 참고 섹션용). '부동액+냉각수+성에+체인'을 합산 척도로 묶어
# 본표(단일 척도)와 '별도로' 참고 표시. 합산 코어(src.core.search_volume)를 이 시드에만 적용.
WINTER_CARE_BUNDLE = ["부동액", "냉각수", "성에제거제", "성에제거기", "성에커버",
                      "스노우체인", "타이어체인", "체인스프레이"]

# 무필터 실험에서 드리프트가 컸던(타도메인 혼입) 씨앗의 '차량특정 형태'. 단일 검색량을
# 기본형과 나란히 측정해 격차를 보여준다 → 시경이 어느 형태를 씨앗으로 쓸지 판단용.
CAR_VARIANTS: dict[str, str] = {
    "햇빛가리개": "차량용햇빛가리개",
    "발수코팅제": "차량용발수코팅제",
    "냉각수": "자동차냉각수",
    "부동액": "자동차부동액",
    "에어컨필터": "자동차에어컨필터",
    "성에제거기": "자동차성에제거기",
    "유리발수": "자동차유리발수",
    "꽃가루": "자동차꽃가루",
}


# ─────────────────────────────────────────────────────────────────────────────
# 규모: 단일 키워드 검색광고 볼륨 (B-1 이전 원래 방식)
# ─────────────────────────────────────────────────────────────────────────────

def _norm(kw: str) -> str:
    """매칭용 정규화: 공백 제거(키워드도구 응답은 공백 없이 오는 경우가 있음)."""
    return "".join(str(kw).split())


def fetch_single_volumes(keywords: list[str], *, adapter=None) -> dict[str, dict]:
    """
    각 키워드의 '단일' 월검색량(monthlyPcQcCnt + monthlyMobileQcCnt). 연관어 합산 아님.

    keywordList 에서 relKeyword 가 '정확히 일치'하는 행만 그 키워드 값으로 쓴다(exact-match
    라 hint 5개 배치해도 섞이지 않음). 반환: {키워드: {"volume": int|None, "low": bool}}.
    adapter 주입 시 그것을 사용(테스트의 결정론 fixture 주입용), 없으면 라이브 NaverAdapter.
    """
    if adapter is None:
        adapter = NaverAdapter(keywords)  # 생성자가 NAVER_AD_* 키 검증.
    by_norm: dict[str, dict] = {}
    for i in range(0, len(keywords), _KEYWORDS_PER_AD_CALL):
        chunk = keywords[i:i + _KEYWORDS_PER_AD_CALL]
        if i > 0:
            adapter._sleep(adapter.rate_limit_seconds)  # 호출 간 rate limit
        for row in adapter._request_keywordstool(chunk):
            rel = str(row.get(FIELD_REL_KEYWORD) or "").strip()
            if rel:
                by_norm.setdefault(_norm(rel), row)  # 첫 출현만(중복 방지)

    out: dict[str, dict] = {}
    for kw in keywords:
        row = by_norm.get(_norm(kw))
        if row is None:
            out[kw] = {"volume": None, "low": False}
            continue
        raw_pc = row.get(FIELD_MONTHLY_PC)
        raw_mo = row.get(FIELD_MONTHLY_MOBILE)
        vol = int(_parse_volume(raw_pc) + _parse_volume(raw_mo))
        low = vol == 0 and any(str(v).strip().startswith("<") for v in (raw_pc, raw_mo))
        out[kw] = {"volume": vol, "low": low}
    return out


def _fmt_volume(rec: dict) -> str:
    v = rec.get("volume")
    if v is None:
        return "확인필요"   # 응답에 키워드 행 없음
    if rec.get("low"):
        return "<10"
    return f"{v:,}"


def _vol_num(rec: dict) -> int:
    """정렬/필터용 숫자 규모. 확인필요(None)·'<10' 은 0(필터를 자연히 못 넘김)."""
    return rec["volume"] if isinstance(rec.get("volume"), int) else 0


# ─────────────────────────────────────────────────────────────────────────────
# 계절 '모양': 데이터랩 수집 + 지표 계산
# ─────────────────────────────────────────────────────────────────────────────

def fetch_shape(season_keywords: dict[str, list[str]], cid: str, csec: str,
                *, request_datalab=None) -> dict[str, dict]:
    """
    데이터랩 검색어트렌드로 키워드별 월별 ratio 수집(계절성용). 그룹 5개/요청 배치.

    0단계 모듈의 _request_datalab/상수만 재사용(파일 무수정). 반환: {kw: {season, periods, ratios}}.
    request_datalab 주입 시 그것을 사용(테스트의 결정론 fixture 주입용).
    """
    req = request_datalab or s0._request_datalab
    flat = [(kw, season) for season, kws in season_keywords.items() for kw in kws]
    season_of = {kw: s for kw, s in flat}
    out: dict[str, dict] = {}
    for i in range(0, len(flat), s0._MAX_GROUPS_PER_REQUEST):
        chunk = flat[i:i + s0._MAX_GROUPS_PER_REQUEST]
        groups = [{"groupName": kw, "keywords": [kw]} for kw, _ in chunk]
        if i > 0:
            time.sleep(s0._RATE_LIMIT_SECONDS)
        resp = req(groups, cid, csec)
        for result in resp.get("results", []):
            kw = result.get("title", "")
            data = result.get("data", [])
            out[kw] = {
                "season": season_of.get(kw, "?"),
                "periods": [d["period"] for d in data],
                "ratios": [float(d["ratio"]) for d in data],
            }
    return out


def per_year_peaks(periods: list[str], ratios: list[float]) -> dict[int, int]:
    """연(YYYY)별로 묶어 각 해의 정점월(1~12)을 구한다. 데이터 없는 해는 생략."""
    by_year: dict[int, list[tuple[int, float]]] = {}
    for period, ratio in zip(periods, ratios):
        by_year.setdefault(int(period[:4]), []).append((int(period[5:7]), ratio))
    return {year: max(pairs, key=lambda mr: mr[1])[0] for year, pairs in by_year.items()}


def _cyclic_spread(months: list[int]) -> int:
    """월 리스트의 순환(12) 최대 간격."""
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
    return ("안정" if spread <= STABILITY_TOLERANCE_MONTHS else "불안정"), spread


def classify(index: float) -> str:
    if index >= SEASONALITY_STRONG:
        return "진짜 계절상품"
    if index >= SEASONALITY_WEAK:
        return "약한 계절성"
    return "상시상품"


def analyze_shape(rec: dict) -> dict | None:
    """데이터랩 시계열(rec) → 계절성 지표. 시계열 없으면 None."""
    if not rec.get("ratios"):
        return None
    profile = s0.monthly_profile(rec["periods"], rec["ratios"])  # 0단계 함수 재사용
    avg = sum(profile) / len(profile)
    peak_val = max(profile)
    peak_month = profile.index(peak_val) + 1
    index = (peak_val / avg) if avg > 0 else 0.0
    peaks = per_year_peaks(rec["periods"], rec["ratios"])
    stab_label, spread = stability(peaks)
    return {
        "profile": profile, "avg": avg, "index": index, "peak_month": peak_month,
        "rising_month": s0.rising_start_month(profile, avg),
        "peaks": peaks, "stability": stab_label, "spread": spread, "label": classify(index),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 출력 — 키워드 상세
# ─────────────────────────────────────────────────────────────────────────────

def print_keyword(kw: str, season: str, rec: dict, vol_rec: dict) -> dict | None:
    """키워드 1개: 계절모양 + 단일 규모 + 연도별 정점/안정성 출력 후 종합행 반환."""
    print("=" * 78)
    print(f"[{season}] {kw}    단일 키워드 월검색량(규모) = {_fmt_volume(vol_rec)}")
    print("=" * 78)

    a = analyze_shape(rec)
    if a is None:
        print("  데이터랩 시계열 없음 — 계절성 판정 불가(검색량 극소 가능).\n")
        return None

    disp_max = max(a["profile"]) or 1.0   # 정점월=100 재정규화 기준(스케일 불변).
    print(f"  최근 3년 월별 상대 검색량  [정점월=100, 평균선={a['avg'] / disp_max * 100:.0f}]")
    for m in range(12):
        v = a["profile"][m] / disp_max * 100
        marks = []
        if m + 1 == a["peak_month"]:
            marks.append("◀ 정점")
        if m + 1 == a["rising_month"]:
            marks.append("◀ 상승시작")
        flag = "  " + " ".join(marks) if marks else ""
        print(f"    {s0._MONTH_LABELS[m]:>3} {v:5.0f} |{s0._bar(v, 100):<{s0._BAR_WIDTH}}{flag}")

    yearly = "  ".join(f"{y}:{a['peaks'][y]}월" for y in sorted(a["peaks"]))
    print()
    print(f"  계절성 지수(최대월/연평균) = {a['index']:.2f}  →  {a['label']}")
    print(f"  정점월 {a['peak_month']}월 · 상승 시작 {a['rising_month']}월")
    print(f"  연도별 정점월: {yearly}   → {a['stability']}"
          f"{'' if a['stability'] == '안정' else f' (정점월 최대 {a['spread']}개월 흔들림)'}")
    print()
    return {
        "keyword": kw, "season": season, "index": a["index"], "label": a["label"],
        "peak_month": a["peak_month"], "rising_month": a["rising_month"],
        "volume": vol_rec, "stability": a["stability"], "spread": a["spread"], "peaks": a["peaks"],
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
    """컬럼: 제품 | 시즌 | 계절성지수 | 정점/상승월 | 절대규모(단일) | 안정성."""
    print(f"  {'제품':<12} {'시즌':<8} {'계절성지수':>7} {'정점/상승':>9} "
          f"{'절대규모(단일)':>13} {'안정성':<8}")
    print(f"  {'-'*12} {'-'*8} {'-'*7} {'-'*9} {'-'*13} {'-'*8}")
    for r in rows:
        pr = f"{r['peak_month']}월/{r['rising_month']}월"
        print(f"  {r['keyword']:<12} {r['season']:<8} {r['index']:>7.2f} {pr:>9} "
              f"{_fmt_volume(r['volume']):>13} {r['stability']:<8}")


def print_summary(rows: list[dict], sort_by: str, apply_filter: bool) -> None:
    sort_label = "계절성순" if sort_by == "seasonality" else "규모순"
    thr = config.MARKET_SIZE_THRESHOLD
    threshold_known = isinstance(thr, (int, float)) and not isinstance(thr, bool)
    effective_filter = apply_filter and threshold_known

    print("=" * 78)
    print("통합 랭킹 — 전 시즌 후보 한 표  (단일 점수 없음: 규모·계절성은 별도 축)")
    print("=" * 78)
    thr_text = f"{thr:,}" if threshold_known else "[확인 필요 — 미확정]"
    print(f"  정렬: {sort_label}  |  추천필터(규모 ≥ {thr_text} AND 계절성 ≥ {FILTER_MIN_INDEX}): "
          f"{'ON' if effective_filter else 'OFF'}")
    if apply_filter and not threshold_known:
        print(f"  [확인 필요] config.MARKET_SIZE_THRESHOLD 미확정 → 규모 필터 보류, 전체를 "
              f"정렬만 해서 표시(원래 단일 척도 참고선 {SINGLE_SCALE_THRESHOLD_REF:,}, 시경 확정 후 활성).")
    print(f"  [전환] 정렬 '규모'/'계절성' · 필터 끄기 '전체'\n")

    def passes(r: dict) -> bool:
        return _vol_num(r["volume"]) >= thr and r["index"] >= FILTER_MIN_INDEX

    kept = [r for r in rows if (not effective_filter) or passes(r)]
    dropped = [r for r in rows if effective_filter and not passes(r)]

    if kept:
        _print_table(_sorted(kept, sort_by))
    else:
        print("  (추천 후보 없음 — 규모·계절성 둘 다 만족하는 제품이 없음)")
    print(f"\n  → {'추천 후보' if effective_filter else '표시'} {len(kept)}개 / 전체 분석 {len(rows)}개")

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

    print("\n  [주의] 규모는 '단일 키워드' 월검색량(기기군 합산 아님 — B-1 되돌림). "
          "발주 데드라인 미포함.")


def print_winter_reference(bundle: list[str], *, adapter=None) -> None:
    """
    B-6 #6 참고 섹션: '겨울 케어 묶음'을 합산 척도로 별도 표시(본표와 절대 섞지 않음).

    합산 코어(src.core.search_volume)를 겨울 시드에만 적용 → 시드별 기기군 합산 + 총합.
    ★척도 충돌 주의: 이 숫자는 '합산 척도'다. 위 본표('단일 척도') 숫자와 합치거나 비교 금지.
    """
    from src.core.search_volume import fetch_aggregated_volume  # 코어 합산(겨울 시드 한정)

    agg = fetch_aggregated_volume(bundle, adapter=adapter)
    print("\n" + "=" * 78)
    print("참고: 겨울 케어 묶음 (합산 척도, 본표와 별도) — 단일 척도 본표 숫자와 섞지 말 것")
    print("=" * 78)
    print(f"  {'겨울 시드':<12} {'기기군 합산':>12} {'멤버수':>5}")
    print(f"  {'-'*12} {'-'*12} {'-'*5}")
    total = 0
    for seed in bundle:
        a = agg.get(seed, {})
        vol = int(a.get("total_volume", 0))
        total += vol
        print(f"  {seed:<12} {vol:>12,} {len(a.get('member_keywords', [])):>5}")
    print(f"  {'-'*12} {'-'*12}")
    print(f"  {'겨울 합산':<12} {total:>12,}")
    print("  (합산 코어 _is_consumable 기준. 단일 척도 본표와 별개의 참고 지표.)")


# ─────────────────────────────────────────────────────────────────────────────
# 측정 모드: 전 씨앗 단일 검색량 + ≥임계 + 계절성지수 + 차량특정 변형 비교 (정지)
# ─────────────────────────────────────────────────────────────────────────────

def run_measurement(season_keywords: dict[str, list[str]]) -> None:
    cid, csec = s0._load_credentials()                 # 데이터랩 키(계절성용) 검증
    flat = [(kw, s) for s, kws in season_keywords.items() for kw in kws]
    seeds = [kw for kw, _ in flat]
    season_of = {kw: s for kw, s in flat}
    variants = {b: v for b, v in CAR_VARIANTS.items() if b in seeds}
    all_kws = seeds + list(variants.values())

    print("=" * 84)
    print(f"[B-2 재측정] 단일 키워드 척도 — 씨앗 {len(seeds)}개 (+차량특정 변형 {len(variants)}개)")
    print(f"  단일 척도 참고 임계 = {SINGLE_SCALE_THRESHOLD_REF:,}  | "
          f"계절성 임계 = {SEASONALITY_STRONG}  | config.MARKET_SIZE_THRESHOLD = "
          f"{config.MARKET_SIZE_THRESHOLD}")
    print("=" * 84)

    print("데이터랩 계절성 수집 중...")
    shape = fetch_shape(season_keywords, cid, csec)
    print("검색광고 단일 키워드 검색량 수집 중...\n")
    vols = fetch_single_volumes(all_kws)

    rows = []
    for kw in seeds:
        a = analyze_shape(shape.get(kw, {}))
        rows.append({
            "kw": kw, "season": season_of[kw], "vol": vols[kw],
            "index": a["index"] if a else None, "label": a["label"] if a else "—",
        })
    rows.sort(key=lambda r: _vol_num(r["vol"]), reverse=True)

    print(f"  {'제품':<12} {'시즌':<8} {'단일검색량':>10} {'≥2만':>4} {'계절성':>6}  분류")
    print(f"  {'-'*12} {'-'*8} {'-'*10} {'-'*4} {'-'*6}  {'-'*12}")
    for r in rows:
        ge = "O" if _vol_num(r["vol"]) >= SINGLE_SCALE_THRESHOLD_REF else "·"
        idx = f"{r['index']:.2f}" if r["index"] is not None else "—"
        print(f"  {r['kw']:<12} {r['season']:<8} {_fmt_volume(r['vol']):>10} {ge:>4} "
              f"{idx:>6}  {r['label']}")

    # 차량특정 형태 비교 — 기본형 vs 차량용X 단일 검색량 격차.
    print("\n  ── 차량특정 형태 비교 (드리프트 컸던 씨앗: 어느 형태를 씨앗으로 쓸지 판단용) ──")
    print(f"     {'기본형':<12} {'단일':>10}   {'차량특정형':<16} {'단일':>10}   격차")
    for base, var in variants.items():
        bv = _vol_num(vols[base])
        vv = _vol_num(vols[var])
        if vv == 0 and bv == 0:
            note = "둘 다 ~0"
        elif vv == 0:
            note = "차량형 ~0 (기본형이 시장)"
        elif bv == 0:
            note = "기본형 ~0 (차량형이 시장)"
        else:
            note = f"차량형이 기본형의 {vv / bv * 100:.0f}%"
        print(f"     {base:<12} {_fmt_volume(vols[base]):>10}   "
              f"{var:<16} {_fmt_volume(vols[var]):>10}   {note}")

    print("\n  [정지] 시경 확정 대기: (a) 차량특정 씨앗 형태, (b) 임계값 → config 입력 후 "
          "B-5(unit test)/B-6(반복출력).")


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
        run_measurement(SEASON_KEYWORDS)   # 단일 검색량 + 계절성 + 차량변형 비교 후 정지.
        return

    cid, csec = s0._load_credentials()       # 데이터랩 키 검증
    flat = [(kw, season) for season, kws in SEASON_KEYWORDS.items() for kw in kws]
    keywords = [kw for kw, _ in flat]
    season_of = {kw: season for kw, season in flat}

    print(f"계절 캘린더 — 씨앗 {len(keywords)}개, 기간 {s0.START_DATE}~{s0.END_DATE}")
    print(f"임계 STRONG={SEASONALITY_STRONG} WEAK={SEASONALITY_WEAK} "
          f"안정성허용={STABILITY_TOLERANCE_MONTHS}개월 (규모=단일 키워드)\n")

    print("데이터랩에서 계절 모양 수집 중...")
    shape = fetch_shape(SEASON_KEYWORDS, cid, csec)          # 데이터랩(계절 모양)
    print("검색광고에서 단일 키워드 검색량 수집 중...\n")
    volumes = fetch_single_volumes(keywords)                 # 단일 키워드 규모

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

    # B-6 #6: 겨울 케어 묶음 합산 참고 섹션(본표와 분리, 합산 척도).
    print("\n검색광고에서 겨울 케어 묶음 합산 수집 중...")
    print_winter_reference(WINTER_CARE_BUNDLE)


if __name__ == "__main__":
    main()
