"""
car_demand.py — Phase C-3 규모 랭킹 + C-4 추세 랭킹.

부품 시드 수확 → 차종 인식 → 모델별 합산 규모(C-3) + 데이터랩 월별 추세(C-4).
규모(검색광고 절대값)와 추세(데이터랩 상대값)는 별도 컬럼 — 합산/단일 점수 금지.

추세는 C-3 랭킹 모델 '전체'(≥MODEL_MIN_VOLUME)에 계산한다(상위만 X — 신차 발굴 보존).
작은 모델/멤버1은 '저신뢰(방향만)'. baseline≈0 & 최근 신호는 '신규 후보'(떠오르는 신차).

실행: python scripts/car_demand.py            # 규모순(기본)
      python scripts/car_demand.py 추세         # 추세순
      python scripts/car_demand.py 신차         # '떠오르는 신차 후보' 뷰만
검색광고 키(NAVER_AD_*) + 데이터랩 키(NAVER_CLIENT_*) 둘 다 필요.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
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
from src.adapters.naver_adapter import NaverAdapter
from src.core.car_demand import (
    DATALAB_MAX_GROUPS_PER_REQUEST,
    compute_trend,
    harvest_models,
    model_member_keywords,
    rank_models,
    select_group_keywords,
)
from src.core.car_models import load_car_models

_DATALAB_URL = "https://naverapihub.apigw.ntruss.com/search-trend/v1/search"


def _datalab_creds() -> tuple[str, str]:
    cid = os.environ.get("NAVER_CLIENT_ID", "").strip()
    csec = os.environ.get("NAVER_CLIENT_SECRET", "").strip()
    if not (cid and csec):
        print("[중단] 데이터랩 키(NAVER_CLIENT_ID/SECRET)가 .env 에 없습니다 — 추세(C-4)에 필요.")
        sys.exit(1)
    return cid, csec


def _months_ago(d: date, n: int) -> date:
    m, y = d.month, d.year
    for _ in range(n):
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return date(y, m, 1)


def _request_datalab(groups: list[dict], start: str, end: str, cid: str, csec: str) -> dict:
    body = json.dumps({"startDate": start, "endDate": end, "timeUnit": "month",
                       "keywordGroups": groups}).encode("utf-8")
    req = urllib.request.Request(_DATALAB_URL, data=body, method="POST")
    req.add_header("X-NCP-APIGW-API-KEY-ID", cid)
    req.add_header("X-NCP-APIGW-API-KEY", csec)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"데이터랩 HTTP {e.code}: {e.read().decode('utf-8', 'replace')}") from e


def fetch_trends(canon_keywords: dict[str, list], cid: str, csec: str) -> dict:
    """모델별 멤버 키워드를 데이터랩 한 그룹으로 묶어 월별 추세 계산. 5그룹/요청 배치."""
    # 요청 기간 = baseline + recent + 여유 3개월. endDate 는 '직전 완전 월' 말일까지만 —
    # 부분(진행중) 월을 recent 에 넣으면 평균이 깎여 전 모델이 거짓 ↓ 로 보인다(검산으로 확인).
    span = config.TREND_BASELINE_MONTHS + config.TREND_RECENT_MONTHS + 3
    today = date.today()
    end_d = date(today.year, today.month, 1) - timedelta(days=1)  # 직전 월 말일
    start = _months_ago(end_d, span).isoformat()
    end = end_d.isoformat()

    canons = list(canon_keywords.keys())
    trends: dict = {}
    for i in range(0, len(canons), DATALAB_MAX_GROUPS_PER_REQUEST):
        batch = canons[i:i + DATALAB_MAX_GROUPS_PER_REQUEST]
        groups = [{"groupName": c, "keywords": select_group_keywords(canon_keywords[c])}
                  for c in batch]
        if i > 0:
            time.sleep(0.5)
        resp = _request_datalab(groups, start, end, cid, csec)
        for result in resp.get("results", []):
            data = result.get("data", [])
            periods = [d["period"] for d in data]
            ratios = [float(d["ratio"]) for d in data]
            trends[result.get("title", "")] = compute_trend(
                periods, ratios,
                recent_months=config.TREND_RECENT_MONTHS,
                baseline_months=config.TREND_BASELINE_MONTHS,
                near_zero=config.TREND_BASELINE_NEAR_ZERO,
                up=config.TREND_UP_THRESHOLD, down=config.TREND_DOWN_THRESHOLD)
    return trends


def _low_conf(row, trend) -> bool:
    return (row.members <= 1) or (row.volume < config.TREND_LOWCONF_VOLUME) or trend.data_insufficient


def _trend_cell(trend, low_conf: bool) -> str:
    if trend.new_candidate:
        return "신규 후보"
    if trend.direction == "데이터부족":
        return "데이터부족"
    if low_conf:
        return trend.direction              # 방향만(비율 숨김)
    return f"{trend.ratio:.2f} {trend.direction}"


def _print_limits(thr_text: str) -> None:
    print("  [한계]")
    print("   ① 추세는 '이미 검색량이 쌓인' 모델만 잡는다 — 출시 직후 진짜 신차는 바닥이라 안 보임.")
    print("      그 구간은 시경의 시장감각이 데이터보다 빠르다(시스템은 보완재).")
    print("   ② 데이터랩은 상대값 — 추세 '크기'는 모델 간 직접 비교 불가, '방향' 신호로만.")
    print("   ③ 저신뢰(멤버1·규모 하한 근처) 모델은 방향만 표시. 작은 모델 추세는 노이즈 가능.")
    print(f"   · 규모(검색광고 절대값)와 추세(데이터랩)는 별도 컬럼 — 합산/단일점수 없음. {thr_text}")


def render_table(rows, trends, title):
    """규모·추세 별도 컬럼 표 한 장(세 뷰 공용). 합산검색량(규모)과 추세는 각자 칸 —
    합쳐 단일 매력도 점수로 만들지 않는다. 추세 칸은 저신뢰면 방향만(비율 숨김)."""
    print("\n" + "─" * 92)
    print(title)
    print(f"  {'정규명':<16} {'부품유형':<8} {'합산검색량':>9} {'멤버':>4} {'추세':>12} {'신뢰도':<6}")
    print(f"  {'-'*16} {'-'*8} {'-'*9} {'-'*4} {'-'*12} {'-'*6}")
    for r in rows:
        t = trends.get(r.canonical)
        if t is None:
            cell, conf = "데이터부족", "저신뢰"
        else:
            lc = _low_conf(r, t)
            cell, conf = _trend_cell(t, lc), ("저신뢰" if lc else "정상")
        tag = " (세대미상)" if r.ambiguous else ""
        print(f"  {r.canonical:<16} {r.part_type:<8} {r.volume:>9,} {r.members:>4} "
              f"{cell:>12} {conf:<6}{tag}")


def _sort_rows(rows, trends, sort_by):
    if sort_by != "trend":
        return sorted(rows, key=lambda r: r.volume, reverse=True)
    # 추세순: 신규 후보 먼저 → 상승률 큰 순 → 데이터부족 마지막.
    def key(r):
        t = trends.get(r.canonical)
        if t is None or t.direction == "데이터부족":
            return (0, 0.0)
        if t.new_candidate:
            return (2, float("inf"))
        return (1, t.ratio or 0.0)
    return sorted(rows, key=key, reverse=True)


def main() -> None:
    arg = " ".join(sys.argv[1:]).strip().lower()
    sort_by = "trend" if arg in ("추세", "trend") else "scale"
    rising_view = arg in ("신차", "rising", "신차후보")

    idx = load_car_models()
    seeds = config.CAR_PART_SEEDS
    adapter = NaverAdapter([s for ss in seeds.values() for s in ss])
    cid, csec = _datalab_creds()

    print("=" * 92)
    print("Phase C — 차종 수요: 규모(C-3) + 추세(C-4)  (규모·추세 별도 컬럼, 단일 점수 없음)")
    print("=" * 92)
    _print_limits(f"컷 MODEL_MIN_VOLUME={config.MODEL_MIN_VOLUME}, 추세 {config.TREND_RECENT_MONTHS}"
                  f"개월÷{config.TREND_BASELINE_MONTHS}개월, ↑≥{config.TREND_UP_THRESHOLD}/"
                  f"↓≤{config.TREND_DOWN_THRESHOLD}")

    print("\n검색광고 키워드도구에서 부품 시드 수확 중...")
    agg = harvest_models(adapter, seeds, idx)
    rows = rank_models(agg, idx, config.MODEL_MIN_VOLUME)

    # 추세는 랭킹 모델 '전체'(중복 정규명 1회). 모델 단위로 데이터랩 그룹 묶음.
    canon_kw = model_member_keywords(agg)
    ranked_canons = {r.canonical for r in rows}
    canon_kw = {c: kw for c, kw in canon_kw.items() if c in ranked_canons}
    print(f"데이터랩에서 모델 추세 수집 중... (모델 {len(canon_kw)}개)")
    trends = fetch_trends(canon_kw, cid, csec)

    if rising_view:
        rising = [r for r in rows
                  if (t := trends.get(r.canonical)) and (t.new_candidate or t.direction == "↑")]
        render_table(_sort_rows(rising, trends, "trend"), trends,
                     "[뷰] 떠오르는 신차 후보 = 추세 ↑ 또는 신규 후보 (단순 필터 — 점수 합산 아님)")
        print(f"\n  → 후보 {len(rising)}행")
        return

    render_table(_sort_rows(rows, trends, sort_by), trends,
                 f"본표 (정렬: {'추세순' if sort_by == 'trend' else '규모순'})")
    n_new = sum(1 for r in rows if (t := trends.get(r.canonical)) and t.new_candidate)
    n_up = sum(1 for r in rows if (t := trends.get(r.canonical)) and t.direction == "↑")
    print(f"\n  → {len(rows)}행 · 신규 후보 {n_new} · 상승↑ {n_up}  "
          f"(떠오르는 신차 후보 뷰: 인자 '신차')")


if __name__ == "__main__":
    main()
