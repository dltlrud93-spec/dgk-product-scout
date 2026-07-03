"""
scanner.py — 데이터 전수 스캔 엔진(순수 로직, UI 없음).

데이터(조견표) 전체 차종 × 제품을 전수 조회해 발굴함 행을 만든다.
  · plan_scan  : 이번 실행에서 스캔할 차종(todo)과 스킵 수를 계산(30일 갱신 주기).
  · scan_models: 청크 단위로 검색량→(정상)블로그·최신성 조회→행 생성, 청크마다 콜백.
                 + 연관어 부수확(variants_fn) — 응답에 딸려온 연관 키워드를 조건부 발굴.

검색량·블로그·최신성 조회는 모두 주입(fn)으로 받아 네트워크 없이 테스트한다.
검색량은 API 규약대로 ★5개 묶음 조회, 저장 청크는 config.VAULT_CHUNK_SIZE(=10)개 단위.

핵심 불변(v1.1):
  · 검색량 ≥ 10 → status="정상", 블로그 문서수+최신성 조회.
  · 검색량 0~9 → status="잠복", 블로그/최신성 조회 스킵, 지표 공란.
  · 조회 실패(검색량·블로그) → 행 생성 금지(시트 미기록 = 다음 실행 plan_scan 이 자동 재시도).
  · ★배치 오염 격리: 검색량 배치 예외 시 그 배치를 1개씩 개별 재시도 — 독(毒) 1개가
    나머지를 오염시키지 않는다. 개별 실패만 실패 목록에 (stage, reason) 과 함께 남긴다.
  · scan_models 는 실패 목록 list[dict]{model, keyword, stage, reason} 을 반환(표시는 세션 한정).
"""

from __future__ import annotations

import datetime
from typing import Callable, Optional

import config
from src.core.car_models import normalize_text
from src.core.jogyeonpyo import build_keyword
from src.core.search_volume import member_volume
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


# ── 배치 검색량 + 연관어 응답 공유 (배치당 API 1회) ──────────────────────────

def make_batch_query(
    request_fn: Callable[[list[str]], list[dict]],
    *,
    sleep_fn: Optional[Callable[[float], None]] = None,
    rate_limit: float = 0.0,
) -> tuple[Callable[[list[str]], dict], Callable[[list[str]], dict]]:
    """검색광고 키워드도구 응답을 ★배치당 1회만 호출·캐시하고 (volumes_fn, variants_fn) 반환.

    · volumes_fn(kws)  → {요청 keyword: volume}  (매칭 없으면 0)
    · variants_fn(kws) → {응답의 모든 relKeyword: volume}  (연관어 부수확용)
    같은 kws 로 두 함수를 불러도 request_fn 은 1회만 실행된다(연관어가 검색량과 API 를 공유).
    request_fn 예외는 그대로 전파(호출부의 개별 재시도가 격리).
    """
    cache: dict[tuple, list[dict]] = {}

    def _rows(kws: list[str]) -> list[dict]:
        key = tuple(kws)
        if key not in cache:
            if sleep_fn is not None and rate_limit:
                sleep_fn(rate_limit)
            cache[key] = request_fn(list(kws))
        return cache[key]

    def volumes_fn(kws: list[str]) -> dict:
        rows = _rows(kws)
        by_norm: dict[str, int] = {}
        for row in rows:
            rk = normalize_text(str(row.get("relKeyword", "")))
            if rk and rk not in by_norm:
                by_norm[rk] = member_volume(row)
        return {kw: by_norm.get(normalize_text(kw), 0) for kw in kws}

    def variants_fn(kws: list[str]) -> dict:
        rows = _rows(kws)
        out: dict[str, int] = {}
        for row in rows:
            kw = str(row.get("relKeyword", "")).strip()
            if kw and kw not in out:
                out[kw] = member_volume(row)
        return out

    return volumes_fn, variants_fn


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
    ★build_keyword 가 빈 문자열을 내는 차종은 todo 에서 제외(스캔 불가 — 조용한 매 스캔 드롭 방지).
      제외 수는 no_keyword_models() 로 호출부가 별도 집계(캡션 안내).
    """
    if force:
        return [m for m in models if build_keyword(m, product)], 0

    latest = latest_by_keyword(vault_rows)
    ref = today or _today_kst()
    cutoff = ref - datetime.timedelta(days=skip_days)

    todo: list[str] = []
    skipped = 0
    for m in models:
        kw = build_keyword(m, product)
        if not kw:
            continue  # 빈 키워드(차종명 이상) — 스캔 대상 아님(no_keyword_models 가 집계)
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


def no_keyword_models(product: str, models: list[str]) -> list[str]:
    """build_keyword 가 빈 문자열을 내는(키워드 생성 불가) 차종 목록 — 스캔 제외 대상.

    호출부가 len() 으로 '키워드 생성 불가 N개 제외(차종명 확인 필요)' 캡션에 쓴다.
    """
    return [m for m in models if not build_keyword(m, product)]


def _chunks(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _recent_or_blank(recent_fn: Optional[Callable[[str], int]], kw: str):
    """최신성 조회(선택). 실패·미조회는 공란("")."""
    if recent_fn is None:
        return ""
    try:
        return int(recent_fn(kw))
    except Exception:  # noqa: BLE001 — 최신성 실패는 치명 아님(공란 유지)
        return ""


def _query_batch(
    kws: list[str],
    volumes_fn: Callable[[list[str]], dict],
    variants_fn: Optional[Callable[[list[str]], dict]],
) -> tuple[dict, list[dict], dict]:
    """한 배치(≤5) 검색량 + (선택)연관어 조회. 배치 예외 시 ★1개씩 개별 재시도.

    반환: (volmap, failures, variants)
      · volmap    : {keyword: volume} (성공분만)
      · failures  : [{keyword, stage:"검색량", reason}] (개별 재시도에서도 실패한 것)
      · variants  : {relKeyword: volume} (연관어 부수확 후보 — variants_fn 없으면 {})
    ★독 1개(400 등)가 배치 전체를 오염시키지 않도록: 배치 실패 → 개별 재시도로 성공분 보존.
    """
    volmap: dict = {}
    failures: list[dict] = []
    variants: dict = {}
    try:
        volmap = dict(volumes_fn(kws))
        if variants_fn is not None:
            variants = dict(variants_fn(kws))
    except Exception:  # noqa: BLE001 — 배치 오염 → 개별 재시도로 격리
        for kw in kws:
            try:
                volmap.update(volumes_fn([kw]))
                if variants_fn is not None:
                    variants.update(variants_fn([kw]))
            except Exception as e:  # noqa: BLE001 — 개별 실패만 기록
                failures.append({
                    "keyword": kw, "stage": "검색량",
                    "reason": f"{type(e).__name__}: {e}",
                })
    return volmap, failures, variants


def _scan_chunk(
    chunk_models: list[str],
    product_kw: str,
    volumes_fn: Callable[[list[str]], dict],
    blog_fn: Callable[[str], int],
    recent_fn: Optional[Callable[[str], int]],
    scanned_at: str,
    variants_fn: Optional[Callable[[list[str]], dict]] = None,
) -> tuple[list[list], list[dict], dict]:
    """차종 청크 하나 → (발굴함 행, 실패 목록, 연관어 후보맵). 순수 로직, 주입 fn 만 사용."""
    # (차종, 키워드) 쌍 — 빈 키워드는 스캔 대상에서 제외.
    pairs = [(m, build_keyword(m, product_kw)) for m in chunk_models]
    pairs = [(m, kw) for m, kw in pairs if kw]
    model_of = {kw: m for m, kw in pairs}

    # 검색량: 5개 묶음 조회(배치 예외 → 개별 재시도로 격리). 연관어도 같은 배치에서 수집.
    volmap: dict[str, int] = {}
    failed: set[str] = set()
    failures: list[dict] = []
    variants: dict[str, int] = {}
    for i in range(0, len(pairs), _VOLUME_BATCH):
        sub = pairs[i:i + _VOLUME_BATCH]
        kws = [kw for _m, kw in sub]
        vmap, vfail, vvar = _query_batch(kws, volumes_fn, variants_fn)
        volmap.update(vmap)
        for f in vfail:
            failed.add(f["keyword"])
            failures.append({**f, "model": model_of.get(f["keyword"], "")})
        for k, v in vvar.items():
            variants.setdefault(k, v)

    rows: list[list] = []
    for m, kw in pairs:
        if kw in failed:
            continue  # 검색량 조회 실패 — 행 생성 금지(시트 미기록 = 다음 실행 재시도)
        vol = int(volmap.get(kw, 0))
        if vol < _MIN_NORMAL_VOLUME:
            rows.append(make_vault_row(scanned_at, product_kw, m, kw, vol, status="잠복"))
            continue
        # 정상 — 블로그 문서수(실패 시 행 미생성 + 실패 기록) + 최신성(실패 시 공란).
        try:
            doc = int(blog_fn(kw))
        except Exception as e:  # noqa: BLE001 — 블로그 실패는 다음 실행 재시도(행 미생성)
            failures.append({
                "model": m, "keyword": kw, "stage": "블로그",
                "reason": f"{type(e).__name__}: {e}",
            })
            continue
        ratio = doc / vol
        rows.append(make_vault_row(
            scanned_at, product_kw, m, kw, vol,
            doc_count=doc, ratio=round(ratio, 4), grade=classify_ratio(ratio),
            recent_3m=_recent_or_blank(recent_fn, kw),
            opportunity_score=int(round(opportunity_score(vol, doc))), status="정상",
        ))
    return rows, failures, variants


def scan_models(
    models: list[str],
    product_kw: str,
    *,
    volumes_fn: Callable[[list[str]], dict],
    blog_fn: Callable[[str], int],
    recent_fn: Optional[Callable[[str], int]] = None,
    on_chunk: Callable[[list[list]], None],
    variants_fn: Optional[Callable[[list[str]], dict]] = None,
    index=None,
    vault_rows: Optional[list[dict]] = None,
    variant_cap: int = config.VAULT_VARIANT_CAP,
    skip_days: int = config.VAULT_SKIP_DAYS,
    chunk_size: int = config.VAULT_CHUNK_SIZE,
    scanned_at: Optional[str] = None,
    today: Optional[datetime.date] = None,
) -> list[dict]:
    """차종을 chunk_size 단위로 스캔하고, 청크 완료마다 on_chunk(rows) 호출.

    on_chunk 는 호출부가 즉시 append_vault_rows 하도록 하는 훅 — 중단 내성의 핵심.
    청크가 전부 실패/잠복이어도 on_chunk 는 호출된다(rows 는 빈 리스트일 수 있음).

    반환: 실패 목록 [{model, keyword, stage, reason}] (표시는 세션 한정 — 시트 미기록).

    연관어 부수확(variants_fn·index 둘 다 주면 활성):
      variants_fn 응답의 연관 키워드 중 아래 ①~⑤ 를 전부 통과한 것만 정상 행으로 저장.
        ① 키워드에 product_kw 포함
        ② 라이브 인식 사전 index.recognize(kw).canonical 존재
        ③ volume ≥ 10 (미만은 API 제안일 뿐 — 잠복 대상 아님, 미기록)
        ④ 발굴함에 skip_days 내 행 없음
        ⑤ 같은 스캔 세션 내 중복 아님(요청 키워드 전체 + 이미 채택 변형)
      채택: car_model=인식 정규명, keyword=변형 원문, product=현재 제품. variant_cap 초과 시 중단.

    Args:
        volumes_fn: 키워드 리스트(≤5) → {keyword: volume}. 배치 예외는 개별 재시도로 격리.
        blog_fn:    keyword → 블로그 문서수. 예외 시 그 키워드 행 미생성 + 실패 기록.
        recent_fn:  keyword → 최근 N개월 글 수(선택). 예외 시 recent_3m 공란.
        on_chunk:   청크 완료 콜백(발굴함 행 리스트).
    """
    stamp = scanned_at or now_kst_str()
    failures: list[dict] = []

    do_variants = variants_fn is not None and index is not None
    # ⑤ 세션 중복 방지 기준 — 요청 키워드 전체(빈 키워드 제외).
    seen: set[str] = {kw for m in models if (kw := build_keyword(m, product_kw))}
    latest = latest_by_keyword(vault_rows or [])
    ref_today = today or _today_kst()
    cutoff = ref_today - datetime.timedelta(days=skip_days)
    adopted = 0
    cap_hit = False

    for chunk in _chunks(list(models), chunk_size):
        rows, chunk_fail, variants = _scan_chunk(
            chunk, product_kw, volumes_fn, blog_fn, recent_fn, stamp, variants_fn)
        failures.extend(chunk_fail)

        if do_variants and not cap_hit:
            for vkw, vvol in variants.items():
                if adopted >= variant_cap:
                    cap_hit = True
                    break
                if vkw in seen:                    # ⑤ 중복(요청·기채택)
                    continue
                if product_kw not in vkw:          # ① 제품 포함
                    continue
                if int(vvol) < _MIN_NORMAL_VOLUME:  # ③ 검색량
                    continue
                rec = index.recognize(vkw)          # ② 차종 인식
                if not rec.recognized:
                    continue
                prev = latest.get(vkw)              # ④ 30일 내 미기록
                if prev is not None:
                    d = _parse_scanned_date(str(prev[0].get("scanned_at", "")))
                    if d is not None and d > cutoff:
                        seen.add(vkw)
                        continue
                # 채택 — 블로그(실패 시 미채택+기록) + 최신성.
                try:
                    doc = int(blog_fn(vkw))
                except Exception as e:  # noqa: BLE001
                    failures.append({
                        "model": rec.canonical, "keyword": vkw, "stage": "블로그",
                        "reason": f"{type(e).__name__}: {e}",
                    })
                    seen.add(vkw)
                    continue
                ratio = doc / int(vvol)
                rows.append(make_vault_row(
                    stamp, product_kw, rec.canonical, vkw, int(vvol),
                    doc_count=doc, ratio=round(ratio, 4), grade=classify_ratio(ratio),
                    recent_3m=_recent_or_blank(recent_fn, vkw),
                    opportunity_score=int(round(opportunity_score(int(vvol), doc))),
                    status="정상",
                ))
                seen.add(vkw)
                adopted += 1

        on_chunk(rows)

    return failures
