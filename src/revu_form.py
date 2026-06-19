"""
revu_form.py — 레뷰(REVU) 네이버 베이직 체험단 양식 docx 생성.

빈 표준 양식(templates/revu_basic_template.docx)을 원본으로 로드하여, 표의 정해진
빈칸 셀에만 입력값을 채워 넣은 docx 를 만든다. 새로 그리지 않는다 — 원본 복사 후
대상 셀의 텍스트만 치환하므로 서식·사전안내문·표 구조가 100% 보존된다.

★빈 양식 표 구조(python-docx 분석 결과, 표 인덱스 0~12):
  표0  r1.p0  "2. 콘텐츠 타입 선택:  "      → 콘텐츠 타입(블로그/클립)
  표3  r0.p0  "성함: "                        → 담당자 성함
       r0.p1  "연락처: "                      → 담당자 연락처
       r0.p2  "이메일: "                       → 담당자 이메일
  표4  r0.p0  "[      ] "                      → 캠페인 제목(20자)
  표5  r0.p0  "[      ] "                      → 캠페인 부제목(40자)
  표6  r0.p0  "　　명"                         → 모집인원
  표7  r0.p0  "제품명: "                       → 제품명
       r0.p1  "제공수량:   개"                 → 제공수량
  표8  r1.p0~2 "1. " "2. " "3. "  (블로그 미션) → 미션(콘텐츠=블로그일 때)
       r3.p0~2 "1. " "2. " "3. "  (클립 미션)   → 미션(콘텐츠=클립일 때)
  표10 r1.p0  "제목키워드 (1~3개) : "          → 제목키워드
       r1.p1  "본문키워드 (3~5개) : "          → 본문키워드
  표11 r0.p0  "링크입력: "                     → 제품링크 URL
  표12 (29행 사전 안내문) — ★고정. 절대 건드리지 않는다.

표1·2(이미지 안내), 표9(구매평 미션), 표8 상품ID/해시태그 블록은 양식 기본값 그대로 둔다.
"""

from __future__ import annotations

import io
import pathlib
from dataclasses import dataclass, field

from docx import Document

# 템플릿 경로(프로젝트 루트 기준).
TEMPLATE_PATH = pathlib.Path(__file__).resolve().parent.parent / "templates" / "revu_basic_template.docx"

# 글자수 제한(양식 규칙).
TITLE_MAX = 20
SUBTITLE_MAX = 40

# 자동 채움 기본값(담당자 — 화면에서 수정 가능).
DEFAULT_MANAGER_NAME = "박민우"
DEFAULT_MANAGER_PHONE = "010-3924-1155"
DEFAULT_MANAGER_EMAIL = "dgkorea93@naver.com"

# 가벼운 금지어 목록(완벽한 필터 아님 — 경고용). 질병명 위주.
# 타브랜드·연예인명은 자동 판별이 어려워 직접 확인하도록 안내만 한다.
BANNED_WORDS = [
    "암", "당뇨", "고혈압", "고지혈", "아토피", "비염", "천식", "탈모",
    "관절염", "디스크", "치매", "우울증", "불면증", "변비", "치질",
    "코로나", "독감", "염증", "통증완화", "면역력", "항암", "항균",
    "다이어트", "체지방감소", "혈압", "혈당",
]
# 절대적·과장 표현(양식 4-7 규칙: "최고"·"최상" 등 금지).
ABSOLUTE_WORDS = ["최고", "최상", "최저가", "유일", "100%", "완벽", "1위"]


@dataclass
class RevuFormData:
    """레뷰 양식 입력값. 비어 있는 값은 양식의 빈칸 그대로 둔다."""
    content_type: str = "블로그"          # "블로그" | "클립"
    campaign_title: str = ""              # 캠페인 제목(≤20자)
    campaign_subtitle: str = ""           # 캠페인 부제목(≤40자)
    car_model: str = ""                   # 차종(선택)
    product_name: str = ""                # 제품명
    provide_qty: str = ""                 # 제공수량
    recruit_count: int = 10               # 모집인원
    title_keywords: str = ""              # 제목키워드(콤마 구분, 1~3개)
    body_keywords: str = ""               # 본문키워드(콤마 구분, 3~5개)
    product_url: str = ""                 # 제품링크 URL
    missions: list = field(default_factory=lambda: ["", "", ""])  # 미션 1·2·3
    manager_name: str = DEFAULT_MANAGER_NAME
    manager_phone: str = DEFAULT_MANAGER_PHONE
    manager_email: str = DEFAULT_MANAGER_EMAIL


def default_mission_lines(car_model: str, product_name: str) -> list[str]:
    """차종 입력 시 블로그 미션 기본 문구 자동 생성(수정 가능). 비우면 빈 3줄."""
    cm = (car_model or "").strip()
    pn = (product_name or "").strip()
    if not cm:
        return ["", "", ""]
    prod = pn or "제품"
    return [
        f"{cm} {prod} 교체방법 및 사용 후기를 상세히 작성해주세요.",
        f"{cm} 차주에게 도움이 되는 정보(주행거리·교체주기 등)를 포함해주세요.",
        "실제 장착 사진 5장 이상과 제품 패키지 사진을 첨부해주세요.",
    ]


def find_banned_words(*texts: str) -> list[str]:
    """입력 텍스트들에서 금지어/절대표현을 찾아 중복 없이 반환(경고용)."""
    blob = " ".join(t for t in texts if t)
    hits: list[str] = []
    for w in BANNED_WORDS + ABSOLUTE_WORDS:
        if w and w in blob and w not in hits:
            hits.append(w)
    return hits


def _set_para_text(paragraph, text: str) -> None:
    """문단 텍스트를 통째로 치환하되 첫 run 의 서식을 보존한다.

    첫 run 의 text 만 새 값으로 바꾸고 나머지 run 은 비워, 폰트·크기 등 서식을 유지.
    run 이 없으면(빈 문단) 새 run 을 추가한다."""
    if paragraph.runs:
        paragraph.runs[0].text = text
        for r in paragraph.runs[1:]:
            r.text = ""
    else:
        paragraph.add_run(text)


def _cell_para(doc, table_idx: int, row_idx: int, para_idx: int):
    """(표·행·문단) 좌표로 대상 문단을 안전하게 반환. 범위 밖이면 None."""
    try:
        cell = doc.tables[table_idx].rows[row_idx].cells[0]
        return cell.paragraphs[para_idx]
    except (IndexError, KeyError):
        return None


def fill_document(doc, data: RevuFormData) -> None:
    """로드된 Document 에 입력값을 채운다(in-place). 표12(사전안내문)는 건드리지 않는다.

    값이 비어 있는 항목은 양식 빈칸 그대로 둔다(없는 값을 만들어 넣지 않음)."""

    def put(t, r, p, text):
        para = _cell_para(doc, t, r, p)
        if para is not None:
            _set_para_text(para, text)

    # 콘텐츠 타입 — 표0 r1 p0
    if data.content_type:
        put(0, 1, 0, f"2. 콘텐츠 타입 선택:  {data.content_type}")

    # 담당자 — 표3 r0 p0~2
    put(3, 0, 0, f"성함: {data.manager_name}")
    put(3, 0, 1, f"연락처: {data.manager_phone}")
    put(3, 0, 2, f"이메일: {data.manager_email}")

    # 캠페인 제목/부제목 — 표4·5 r0 p0 (대괄호 안에)
    if data.campaign_title:
        put(4, 0, 0, f"[ {data.campaign_title} ]")
    if data.campaign_subtitle:
        put(5, 0, 0, f"[ {data.campaign_subtitle} ]")

    # 모집인원 — 표6 r0 p0 ("　　명" → "N명")
    if data.recruit_count is not None:
        put(6, 0, 0, f"{data.recruit_count}명")

    # 제품명/제공수량 — 표7 r0 p0·p1
    if data.product_name:
        put(7, 0, 0, f"제품명: {data.product_name}")
    if data.provide_qty:
        put(7, 0, 1, f"제공수량: {data.provide_qty} 개")

    # 미션 1·2·3 — 콘텐츠 타입에 따라 표8 r1(블로그) 또는 r3(클립)
    mission_row = 3 if data.content_type == "클립" else 1
    for i, m in enumerate(data.missions[:3]):
        if m:
            put(8, mission_row, i, f"{i + 1}. {m}")

    # 키워드 — 표10 r1 p0·p1
    if data.title_keywords:
        put(10, 1, 0, f"제목키워드 (1~3개) : {data.title_keywords}")
    if data.body_keywords:
        put(10, 1, 1, f"본문키워드 (3~5개) : {data.body_keywords}")

    # 제품링크 URL — 표11 r0 p0
    if data.product_url:
        put(11, 0, 0, f"링크입력: {data.product_url}")


def build_revu_docx(data: RevuFormData, template_path=None) -> bytes:
    """빈 양식을 로드 → 값 채움 → docx 바이트 반환(st.download_button 용)."""
    path = pathlib.Path(template_path) if template_path else TEMPLATE_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"레뷰 빈 양식 템플릿을 찾을 수 없습니다: {path}\n"
            "templates/revu_basic_template.docx 파일이 있는지 확인하세요."
        )
    doc = Document(str(path))
    fill_document(doc, data)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def suggest_filename(data: RevuFormData) -> str:
    """다운로드 파일명: 레뷰_{제품명}_{차종}.docx (빈 항목은 생략)."""
    parts = ["레뷰"]
    if data.product_name.strip():
        parts.append(data.product_name.strip())
    if data.car_model.strip():
        parts.append(data.car_model.strip())
    name = "_".join(parts).replace("/", "_").replace("\\", "_")
    return f"{name}.docx"
