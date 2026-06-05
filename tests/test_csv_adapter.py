"""
test_csv_adapter.py — CSVAdapter 검증.

실제 키/네트워크 없이 tmp_path 로 샘플 CSV 를 만들어:
  - 소모품 필터(연관키워드)
  - 신호 7(검색량 합)·신호 8(경쟁정도/노출광고수) 매핑
  - 저소 표기("< 10") 보수적 파싱
  - 검색량 기반 4분면/랭킹 동작(discover)
  - 컬럼 누락 / 파일 없음 / 인코딩 에러
를 검증한다.
"""

import csv
import warnings

import pytest

import config
from src.adapters.csv_adapter import (
    CSVAdapter,
    CSVColumnError,
    _is_consumable,
    _parse_volume,
)
from src.ranking import discover

HEADER = [
    "검색키워드",
    "연관키워드",
    "월검색수(PC)",
    "월검색수(모바일)",
    "월평균노출광고수",
    "경쟁정도",
]


def _write_csv(path, rows, header=HEADER, encoding="utf-8-sig"):
    with open(path, "w", encoding=encoding, newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return str(path)


def _obs_by_name(observations, name):
    return next(o for o in observations if o.category_name == name)


# ---------------------------------------------------------------------------
# 저소 표기 / 숫자 파싱
# ---------------------------------------------------------------------------
def test_parse_volume_handles_low_and_commas():
    assert _parse_volume("1,200") == 1200.0
    assert _parse_volume("< 10") == config.LOW_VOLUME_FALLBACK
    assert _parse_volume("<10") == config.LOW_VOLUME_FALLBACK
    assert _parse_volume("") == config.LOW_VOLUME_FALLBACK
    assert _parse_volume(None) == config.LOW_VOLUME_FALLBACK
    assert _parse_volume("abc") == config.LOW_VOLUME_FALLBACK


# ---------------------------------------------------------------------------
# 소모품 사전 정밀화 + 기기 가드 (실데이터 검증 반영)
# ---------------------------------------------------------------------------
def test_consumable_dict_includes_brush_variants():
    # 문제1: 연관어는 '브러시'가 아니라 '브러쉬'로 표기됨 → 두 표기 모두 매칭돼야.
    assert _is_consumable("로봇청소기 브러쉬") is True
    assert _is_consumable("로봇청소기 브러시") is True
    assert _is_consumable("사이드브러쉬") is True
    assert _is_consumable("메인브러쉬") is True
    # 새로 추가된 소모품형 토큰.
    assert _is_consumable("헤파필터") is True
    assert _is_consumable("물걸레패드") is True
    assert _is_consumable("걸레받침") is True
    assert _is_consumable("정품소모품") is True


def test_brush_cleaner_matches_strong_part_over_device_guard():
    # '브러쉬청소기': 기기어('청소기')가 있어도 STRONG('브러쉬')가 있으니 소모품 유지.
    assert _is_consumable("브러쉬청소기") is True
    # '로봇청소기물걸레패드': STRONG('물걸레패드') 있음 → 소모품.
    assert _is_consumable("로봇청소기물걸레패드") is True


def test_device_guard_excludes_mop_cleaner_and_replace():
    # 문제2: '물걸레','걸레' 단독 제거 → '물걸레청소기'는 소모품 토큰 자체가 없어 매칭 안 됨.
    assert _is_consumable("물걸레청소기") is False
    assert _is_consumable("걸레청소기") is False
    # 기기 가드: STRONG 없이 기기어 + 약한 동작어('교체')만 → 기기 교체로 보고 제외.
    assert _is_consumable("로봇청소기교체") is False
    # 약한 동작어라도 기기어가 없으면 소모품 신호로 유지(기존 동작 보존).
    assert _is_consumable("립스틱 교체팁") is True


# ---------------------------------------------------------------------------
# 소모품 필터 + 신호 7 매핑
# ---------------------------------------------------------------------------
def test_consumable_filter_and_signal7(tmp_path):
    rows = [
        # 로봇청소기: 소모품 2행 + 비소모품 1행
        ["로봇청소기", "로봇청소기 필터", "5000", "8000", "3", "중간"],
        ["로봇청소기", "로봇청소기 브러시", "2000", "3000", "2", "낮음"],
        ["로봇청소기", "로봇청소기 추천", "9000", "9000", "8", "높음"],  # 비소모품 → 제외
    ]
    path = _write_csv(tmp_path / "k.csv", rows)
    obs = CSVAdapter(path).fetch_category_observations()

    assert len(obs) == 1
    robot = _obs_by_name(obs, "로봇청소기 호환 소모품")
    # 신호 7: 소모품 2행만 합산 = (5000+8000)+(2000+3000) = 18000. 비소모품 제외.
    assert robot.category_search_volume == 18000
    assert robot.has_consumable is True
    assert robot.discovery_pattern == "호환소모품"


def test_low_volume_marker_not_inflating(tmp_path):
    rows = [
        ["가습기", "가습기 필터", "< 10", "< 10", "1", "낮음"],
    ]
    path = _write_csv(tmp_path / "low.csv", rows)
    obs = CSVAdapter(path).fetch_category_observations()
    # "< 10" → LOW_VOLUME_FALLBACK(보수적) 이므로 검색량 0.
    assert obs[0].category_search_volume == int(config.LOW_VOLUME_FALLBACK) * 2


# ---------------------------------------------------------------------------
# 신호 8 매핑 (경쟁정도 + 노출광고수)
# ---------------------------------------------------------------------------
def test_signal8_mapping_ad_war(tmp_path):
    rows = [
        # 높은 경쟁정도 + 높은 노출광고수 → 광고싸움 신호 둘 다 충족
        ["립스틱", "립스틱 리필", "30000", "40000", "9", "높음"],
        ["립스틱", "립스틱 교체팁", "20000", "25000", "8", "높음"],
    ]
    path = _write_csv(tmp_path / "ad.csv", rows)
    obs = CSVAdapter(path).fetch_category_observations()[0]
    assert obs.comp_idx == "높음"
    assert obs.avg_ad_depth == pytest.approx(8.5)

    from src.signals import detect_ad_war

    is_ad_war, reasons = detect_ad_war(obs)
    assert is_ad_war is True
    assert len(reasons) >= config.AD_WAR_SIGNAL_COUNT_TO_FLAG


def test_signal8_value_fight_not_ad_war(tmp_path):
    rows = [
        ["가습기", "가습기 필터", "30000", "40000", "1", "낮음"],
    ]
    path = _write_csv(tmp_path / "vf.csv", rows)
    obs = CSVAdapter(path).fetch_category_observations()[0]
    from src.signals import detect_ad_war

    is_ad_war, _ = detect_ad_war(obs)
    assert is_ad_war is False


def test_comp_idx_aggregated_to_representative(tmp_path):
    rows = [
        ["청소기", "청소기 필터", "100", "100", "1", "낮음"],
        ["청소기", "청소기 브러시", "100", "100", "1", "중간"],
        ["청소기", "청소기 헤드", "100", "100", "1", "중간"],
    ]
    path = _write_csv(tmp_path / "c.csv", rows)
    obs = CSVAdapter(path).fetch_category_observations()[0]
    # 서수 평균 round([0,1,1]) = 1 → "중간".
    assert obs.comp_idx == "중간"


# ---------------------------------------------------------------------------
# 4분면 / 랭킹 (검색량 기반) end-to-end
# ---------------------------------------------------------------------------
def test_discover_runs_on_csv(tmp_path):
    rows = [
        # 큰 검색량 + 가치 싸움(낮은 경쟁/노출) → 최우선 기대
        ["로봇청소기", "로봇청소기 필터", "80000", "90000", "1", "낮음"],
        ["로봇청소기", "로봇청소기 브러시", "40000", "50000", "1", "낮음"],
        # 큰 검색량 + 광고 싸움(높은 경쟁+노출) → 함정(기본 제외) 기대
        ["에센스", "에센스 리필", "90000", "90000", "9", "높음"],
        ["에센스", "에센스 교체", "80000", "80000", "9", "높음"],
        # 작은 검색량 + 가치 싸움 → 틈새
        ["미니가습기", "미니가습기 필터", "3000", "4000", "1", "낮음"],
    ]
    path = _write_csv(tmp_path / "rank.csv", rows)

    ranked = discover(CSVAdapter(path), include_trap=False)
    names = [c.category_name for c in ranked]
    # 함정(에센스)은 기본 제외.
    assert "에센스 호환 소모품" not in names
    # 최우선(로봇청소기)이 틈새(미니가습기)보다 위.
    assert names.index("로봇청소기 호환 소모품") < names.index("미니가습기 호환 소모품")
    assert ranked[0].winnability == "최우선"

    # 함정 포함 보기에서는 에센스가 보이되 최하단.
    ranked_all = discover(CSVAdapter(path), include_trap=True)
    all_names = [c.category_name for c in ranked_all]
    assert "에센스 호환 소모품" in all_names
    essence = next(c for c in ranked_all if c.category_name == "에센스 호환 소모품")
    assert essence.winnability == "함정"


# ---------------------------------------------------------------------------
# 오빠두 엑셀 CSV 실제 형태 (실파일 검증 반영)
#   - 상단 빈 4행 + 제목행('전체 검색결과','필터')
#   - 좌측 항목/값 필터패널 컬럼(인덱스 0~3) → 데이터표는 인덱스 4부터
#   - 헤더 셀 안에 줄바꿈('월검색수\n(PC)', '월평균노출\n광고수')
#   - 연관키워드 컬럼명이 '연간키워드'(오빠두 오기)
# ---------------------------------------------------------------------------
def test_oppadu_format_header_autodetect_and_filterpanel(tmp_path):
    # 좌측 4칸은 필터패널(항목/값), 오른쪽 6칸이 실제 데이터표.
    oppadu_rows = [
        [],                                              # 0: 빈 줄
        ["전체 검색결과"],                                 # 1: 제목행
        ["필터"],                                         # 2: 제목행
        [],                                              # 3: 빈 줄
        # 4: 헤더행 — 좌측 필터패널 + 줄바꿈 포함 데이터 헤더 + '연간키워드'
        ["항목", "값", "", "",
         "검색키워드", "연간키워드", "월검색수\n(PC)",
         "월검색수\n(모바일)", "월평균노출\n광고수", "경쟁정도"],
        # 5~: 데이터행(좌측 필터패널 칸은 비거나 값이 섞여 들어옴)
        ["카테고리", "전체", "", "",
         "로봇청소기", "로봇청소기 필터", "5,000", "8,000", "3", "중간"],
        ["", "", "", "",
         "로봇청소기", "로봇청소기 브러시", "2,000", "3,000", "2", "중간"],
        ["", "", "", "",
         "로봇청소기", "로봇청소기 추천", "9,000", "9,000", "8", "높음"],  # 비소모품→제외
        # 검색키워드 비어있는 필터패널 잔여행 → skip 되어야 함
        ["정렬", "검색수순", "", "", "", "", "", "", "", ""],
    ]
    path = tmp_path / "oppadu.csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(oppadu_rows)

    obs = CSVAdapter(str(path)).fetch_category_observations()

    # 헤더 자동탐지(5번째 논리행)·정규화 매칭·'연간키워드' 별칭이 모두 동작.
    assert len(obs) == 1
    robot = _obs_by_name(obs, "로봇청소기 호환 소모품")
    # 소모품 2행만 합산: (5000+8000)+(2000+3000)=18000. 비소모품·필터패널 무시.
    assert robot.category_search_volume == 18000
    assert robot.comp_idx == "중간"  # 소모품 2행 모두 '중간' → 대표값 '중간'


def test_oppadu_format_comp_idx_and_ignores_left_columns(tmp_path):
    # 좌측 필터패널에 '경쟁정도' 같은 단어가 섞여도 데이터표 컬럼이 우선이어야.
    oppadu_rows = [
        [],
        ["전체 검색결과"],
        ["검색키워드", "연간키워드", "월검색수\n(PC)",
         "월검색수\n(모바일)", "월평균노출\n광고수", "경쟁정도"],
        ["청소기", "청소기 필터", "100", "100", "1", "중간"],
        ["청소기", "청소기 헤드", "100", "100", "1", "중간"],
    ]
    path = tmp_path / "oppadu2.csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(oppadu_rows)

    obs = CSVAdapter(str(path)).fetch_category_observations()[0]
    assert obs.category_name == "청소기 호환 소모품"
    assert obs.comp_idx == "중간"  # [중간,중간] → 중간
    assert obs.category_search_volume == 400  # (100+100)*2


def test_header_primary_secondary_path_does_not_warn(tmp_path):
    # 정상 경로(검색키워드+경쟁정도 동시)는 경고를 내지 않아야 한다.
    rows = [["로봇청소기", "로봇청소기 필터", "5000", "5000", "1", "낮음"]]
    path = _write_csv(tmp_path / "ok.csv", rows)
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # 경고가 나면 즉시 에러로 실패
        obs = CSVAdapter(path).fetch_category_observations()
    assert obs[0].category_name == "로봇청소기 호환 소모품"


def test_header_fallback_path_emits_warning(tmp_path):
    # '경쟁정도'가 없어 폴백('검색키워드'만)으로 헤더를 잡으면 그 사실을 경고로 남긴다.
    fb_header = ["검색키워드", "연관키워드", "월검색수(PC)",
                 "월검색수(모바일)", "월평균노출광고수"]  # 경쟁정도 없음
    path = _write_csv(
        tmp_path / "fb.csv",
        [["로봇청소기", "로봇청소기 필터", "100", "100", "1"]],
        header=fb_header,
    )
    with pytest.warns(UserWarning, match="폴백 경로"):
        with pytest.raises(CSVColumnError) as ei:
            CSVAdapter(path).fetch_category_observations()
    # 폴백으로 헤더는 잡되, 누락 컬럼(경쟁정도=comp_idx)은 그대로 보고된다.
    assert "comp_idx" in str(ei.value)


# ---------------------------------------------------------------------------
# 에러 처리 (조용한 폴백 금지)
# ---------------------------------------------------------------------------
def test_missing_column_raises(tmp_path):
    bad_header = ["검색키워드", "연관키워드", "월검색수(PC)"]  # 모바일/노출/경쟁정도 누락
    path = _write_csv(
        tmp_path / "bad.csv",
        [["로봇청소기", "로봇청소기 필터", "100"]],
        header=bad_header,
    )
    with pytest.raises(CSVColumnError) as ei:
        CSVAdapter(path).fetch_category_observations()
    # 어떤 컬럼이 없는지 메시지에 포함.
    assert "monthly_mobile" in str(ei.value)


def test_file_not_found_raises():
    with pytest.raises(FileNotFoundError):
        CSVAdapter("does_not_exist_12345.csv").fetch_category_observations()


def test_cp949_encoding_supported(tmp_path):
    path = _write_csv(
        tmp_path / "cp949.csv",
        [["로봇청소기", "로봇청소기 필터", "5000", "5000", "1", "낮음"]],
        encoding="cp949",
    )
    obs = CSVAdapter(path).fetch_category_observations()
    assert obs[0].category_name == "로봇청소기 호환 소모품"
    assert obs[0].category_search_volume == 10000
