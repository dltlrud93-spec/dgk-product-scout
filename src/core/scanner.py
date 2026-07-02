"""
scanner.py — 데이터 전수 스캔 엔진(순수 로직, UI 없음).

데이터(조견표) 전체 차종 × 제품을 전수 조회해 발굴함 행을 만든다.
  · plan_scan  : 이번 실행에서 스캔할 차종(todo)과 스킵 수를 계산(30일 갱신 주기).
  · scan_models: 청크 단위로 검색량→(정상)블로그·최신성 조회→행 생성, 청크마다 콜백.

검색량·블로그·최신성 조회는 모두 주입(fn)으로 받아 네트워크 없이 테스트한다.
검색량은 API 규약대로 ★5개 묶음 조회, 저장 청크는 config.VAULT_CHUNK_SIZE(=10)개 단위.

핵심 불변:
  · 검색량 ≥ 10 → status="정상", 블로그 문서수+최신성 조회.
  · 검색량 0~9 → status="잠복", 블로그/최신성 조회 스킵, 지표 공란.
  · 조회 실패(검색량 배치 실패·블로그 실패) → 행 생성 금지(다음 실행 plan_scan 이 재포함).
"""

from __future__ import annotations

import datetime
from typing import Callable, Optional

import config
from src.core.jogyeonpyo import build_keyword
from src.core.teamp_mode import classify_ratio, now_kst_str, opportunity_score
from src.core.vault import latest_by_keyword, make_vault_row

# 검색량 API hint 상한(검색광고 키워드도구) — 절대 초과 금지.
_VOLUME_BATCH = 5

# 잠복/정상 경계 — 검색량 이 값 미만이면 잠복(블로그 조회 스킵).
_MIN_NORMAL_VOLUME = 10


def _today_kst() -> datetime.date:
    """KST 기준 오늘 날짜(고정 오프셋 +9h — zoneinfo 없는 환경 안전)."""
    kst = datetime.timezone(datetime.timedelta(hours=9))
    return datetime.datetime.now(kst).date()


def _parse_scanned_date(s: str) -> Optional[datetime.date]:
    """'YYYY-MM-DD HH:MM' → date. 파싱 불가 시 None."""
    try:
        return datetime.date(int(s[0:4]), int(s[5:7]), int(s[8:10]))
    except (ValueError, IndexError, TypeError):
        return None


def plan_scan(
    product: str,
    models: list[str],
    vault_rows: list[dict],
    *,
    skip_days: int = config.VAULT_SKIP_DAYS,
    force: bool = False,
    today: Optional[datetime.date] = None,
) -> tuple[list[str], int]:
    """이번 실행에서 스캔할 차종 목록(todo)과 스킵 수를 계산.

    Args:
        product:    제품 키워드 접미사(예 '에어컨필터') — 차종 → 키워드 변환용.
        models:     데이터 전체 차종명 리스트.
        vault_rows: read_vault() 결과(list[dict]).
        skip_days:  이 일수 이내에 스캔된 키워드는 제외. force=True 면 전체 포함.

    Returns:
        (todo_models, skipped_count)

    ★시트에 행이 없는 키워드(과거 조회 실패 포함)는 항상 todo 에 포함된다 — 실패 자동 재시도.
    """
    if force:
        return list(models), 0

    latest = latest_by_keyword(vault_rows)
    ref = today or _today_kst()
    cutoff = ref - datetime.timedelta(days=skip_days)

    todo: list[str] = []
    skipped = 0
    for m in models:
        kw = build_keyword(m, product)
        if not kw:
            continue  # 정규화 후 빈 키워드(차종명 이상) — 스캔 대상 아님
        rec = latest.get(kw)
        if rec is None:
            todo.append(m)   # 시트에 행 없음(미스캔·과거 실패) → 재포함
            continue
        scanned_date = _parse_scanned_date(str(rec[0].get("scanned_at", "")))
        if scanned_date is not None and scanned_date > cutoff:
            skipped += 1     # skip_days 이내 최근 스캔 → 제외
        else:
            todo.append(m)   # 오래됨(또는 날짜 파싱 불가) → 재스캔
    return todo, skipped


def _chunks(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _scan_chunk(
    chunk_models: list[str],
    product_kw: str,
    volumes_fn: Callable[[list[str]], dict],
    blog_fn: Callable[[str], int],
    recent_fn: Optional[Callable[[str], int]],
    scanned_at: str,
) -> list[list]:
    """차종 청크 하나 → 발굴함 행 리스트(순수 로직, 주입 fn 만 사용)."""
    # (차종, 키워드) 쌍 — 빈 키워드는 스캔 대상에서 제외.
    pairs = [(m, build_keyword(m, product_kw)) for m in chunk_models]
    pairs = [(m, kw) for m, kw in pairs if kw]

    # 검색량: 5개 묶음 조회. 배치 예외 → 그 배치 키워드 전부 실패(행 미생성).
    volmap: dict[str, int] = {}
    failed: set[str] = set()
    for i in range(0, len(pairs), _VOLUME_BATCH):
        sub = pairs[i:i + _VOLUME_BATCH]
        kws = [kw for _m, kw in sub]
        try:
            volmap.update(volumes_fn(kws))
        except Exception:  # noqa: BLE001 — 429 소진 등 배치 단위 격리
            failed.update(kws)

    rows: list[list] = []
    for m, kw in pairs:
        if kw in failed:
            continue  # 검색량 조회 실패 — 행 생성 금지
        vol = int(volmap.get(kw, 0))
        if vol < _MIN_NORMAL_VOLUME:
            # 잠복 — 블로그/최신성 조회 스킵, 지표 공란.
            rows.append(make_vault_row(scanned_at, product_kw, m, kw, vol, status="잠복"))
            continue
        # 정상 — 블로그 문서수(실패 시 행 미생성) + 최신성(실패 시 공란).
        try:
            doc = int(blog_fn(kw))
        except Exception:  # noqa: BLE001 — 블로그 실패는 다음 실행 재시도(행 미생성)
            continue
        ratio = doc / vol
        grade = classify_ratio(ratio)
        opp = int(round(opportunity_score(vol, doc)))
        recent: object = ""
        if recent_fn is not None:
            try:
                recent = int(recent_fn(kw))
            except Exception:  # noqa: BLE001 — 최신성 실패는 치명 아님(공란 유지)
                recent = ""
        rows.append(make_vault_row(
            scanned_at, product_kw, m, kw, vol,
            doc_count=doc, ratio=round(ratio, 4), grade=grade,
            recent_3m=recent, opportunity_score=opp, status="정상",
        ))
    return rows


def scan_models(
    models: list[str],
    product_kw: str,
    *,
    volumes_fn: Callable[[list[str]], dict],
    blog_fn: Callable[[str], int],
    recent_fn: Optional[Callable[[str], int]] = None,
    on_chunk: Callable[[list[list]], None],
    chunk_size: int = config.VAULT_CHUNK_SIZE,
    scanned_at: Optional[str] = None,
) -> None:
    """차종을 chunk_size 단위로 스캔하고, 청크 완료마다 on_chunk(rows) 호출.

    on_chunk 는 호출부가 즉시 append_vault_rows 하도록 하는 훅 — 중단 내성의 핵심.
    청크가 전부 실패/잠복이어도 on_chunk 는 호출된다(rows 는 빈 리스트일 수 있음).

    Args:
        volumes_fn: 키워드 리스트(≤5) → {keyword: volume}. 예외는 배치 실패로 격리.
        blog_fn:    keyword → 블로그 문서수. 예외 시 그 키워드 행 미생성.
        recent_fn:  keyword → 최근 N개월 글 수(선택). 예외 시 recent_3m 공란.
        on_chunk:   청크 완료 콜백(발굴함 행 리스트).
    """
    stamp = scanned_at or now_kst_str()
    for chunk in _chunks(list(models), chunk_size):
        rows = _scan_chunk(chunk, product_kw, volumes_fn, blog_fn, recent_fn, stamp)
        on_chunk(rows)
