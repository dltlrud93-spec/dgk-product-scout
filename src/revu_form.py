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
       r0.p1  "제공수량:   개"                 → 제공수량(원본 "개" 제거, 입력값 그대로)
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
import re
from dataclasses import dataclass, field

# ★python-docx(docx)는 docx 생성 시점에만 필요하므로 ★지연 import 한다(build_revu_docx 내부).
#   모듈 최상단에서 import 하면 배포본에 python-docx 가 없을 때 이 모듈 전체가 import 불가가 되어,
#   find_banned_words·merge_keywords·추적URL 검증 등 순수 함수와 앱 전체(app.py line 76)가
#   함께 죽는다. 지연 import 로 docx 미설치 시에도 양식 외 기능은 정상 동작한다.

# 템플릿 경로(프로젝트 루트 기준).
TEMPLATE_PATH = pathlib.Path(__file__).resolve().parent.parent / "templates" / "revu_basic_template.docx"

# 글자수 제한(양식 규칙).
TITLE_MAX = 20
SUBTITLE_MAX = 40

# 자동 채움 기본값(담당자 — 화면에서 수정 가능).
DEFAULT_MANAGER_NAME = "박민우"
DEFAULT_MANAGER_PHONE = "010-3924-1155"
DEFAULT_MANAGER_EMAIL = "dgkorea93@naver.com"

# ── 네이버 유입 추적 URL(nt_) 조립 ───────────────────────────────────────────
# 네이버 공식 규칙:
#  · nt_source/nt_medium : 영문·숫자·특수문자 3종(-,_,.)만. 한글·공백 금지. 필수.
#  · nt_detail/nt_keyword: 한글·영문·숫자·특수문자 3종(-,_,.) 허용. 공백 금지. 선택.
#  · 그 외 특수문자(/,?,&,=,# 등)·공백은 모두 금지.
DEFAULT_NT_SOURCE = "naver.blog"
DEFAULT_NT_MEDIUM = "social"
DEFAULT_NT_DETAIL = "revu"

# 허용 문자 패턴(공백은 별도 메시지로 먼저 잡는다).
_NT_ASCII_RE = re.compile(r"^[A-Za-z0-9._-]+$")          # source/medium
_NT_KO_RE = re.compile(r"^[가-힣ㄱ-ㅎㅏ-ㅣA-Za-z0-9._-]+$")  # detail/keyword(한글 허용)
_HANGUL_RE = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]")


def validate_nt_param(name: str, value: str, *, required: bool, allow_korean: bool) -> str | None:
    """nt_ 파라미터 1개 검증. 통과면 None, 위반이면 한국어 경고 메시지 반환.

    검사 순서: 필수 누락 → 공백 → 한글(ascii 전용일 때) → 기타 특수문자."""
    v = value or ""
    if not v:
        return f"{name}: 필수값입니다 — 비우면 네이버가 추적하지 못합니다." if required else None
    if any(ws in v for ws in (" ", "\t", "\n", "　")):
        return f"{name}: 공백은 사용할 수 없습니다."
    if not allow_korean and _HANGUL_RE.search(v):
        return f"{name}: 한글은 사용할 수 없습니다 (영문·숫자·-, _, . 만 가능)."
    pattern = _NT_KO_RE if allow_korean else _NT_ASCII_RE
    if not pattern.match(v):
        return f"{name}: 허용되지 않는 문자가 있습니다 (-, _, . 외 특수문자·공백 금지)."
    return None


def build_tracking_url(
    product_url: str,
    nt_source: str,
    nt_medium: str,
    nt_detail: str = "",
    nt_keyword: str = "",
) -> str:
    """추적 파라미터를 제품 URL 에 조립(검증 없음 — 순수 조립).

    ★"?" 중복 방지: 제품 URL 에 이미 "?"가 있으면 "&"로, 없으면 "?"로 이어붙인다.
    선택 파라미터(detail/keyword)는 값이 있을 때만 포함, 필수 2개는 항상 포함."""
    params = [("nt_source", nt_source), ("nt_medium", nt_medium)]
    if nt_detail:
        params.append(("nt_detail", nt_detail))
    if nt_keyword:
        params.append(("nt_keyword", nt_keyword))
    query = "&".join(f"{k}={v}" for k, v in params)
    sep = "&" if "?" in product_url else "?"
    return f"{product_url}{sep}{query}"


def assemble_tracking_url(
    product_url: str,
    nt_source: str,
    nt_medium: str,
    nt_detail: str = "",
    nt_keyword: str = "",
) -> tuple[str | None, list[str]]:
    """검증 + 조립. 반환 (url 또는 None, 경고 메시지 목록).

    위반이 하나라도 있으면 url=None(생성 차단) 으로 errors 를 채워 돌려준다."""
    errors: list[str] = []
    base = (product_url or "").strip()
    if not base:
        errors.append("제품 URL: 필수값입니다.")
    elif any(ws in base for ws in (" ", "\t", "\n", "　")):
        errors.append("제품 URL: 공백이 포함되어 있습니다.")

    for name, val, required, allow_ko in (
        ("nt_source", nt_source, True, False),
        ("nt_medium", nt_medium, True, False),
        ("nt_detail", nt_detail, False, True),
        ("nt_keyword", nt_keyword, False, True),
    ):
        err = validate_nt_param(name, val, required=required, allow_korean=allow_ko)
        if err:
            errors.append(err)

    if errors:
        return None, errors
    return build_tracking_url(base, nt_source, nt_medium, nt_detail, nt_keyword), []

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
    purchase_combine: str = "아니오"       # 구매평 결합 "예" | "아니오"
    urgent: str = "아니오"                 # 긴급 진행 "예" | "아니오"
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


def merge_keywords(existing: str, additions: list[str]) -> str:
    """기존 콤마구분 키워드 문자열에 새 키워드를 ★덧붙인다(덮어쓰지 않음).

    중복은 대소문자 무시로 제거하고, 등장 순서(기존 먼저)는 보존. ", " 로 join.
    키워드 추천 '칸에 추가' 버튼이 쓴다 — 기존 입력 손실 없이 누적."""
    out: list[str] = []
    seen: set[str] = set()
    for kw in [*existing.split(","), *additions]:
        k = kw.strip()
        if k and k.lower() not in seen:
            seen.add(k.lower())
            out.append(k)
    return ", ".join(out)


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


# Step1 드롭다운(콘텐츠 컨트롤 w:sdt)에 들어갈 선택 표시 텍스트(원본 dropDownList 항목과 동일).
_CONTENT_LABEL = {
    "블로그": "① [단독] 블로그 (Blog Only)",
    "클립": "② [단독] 클립 (Clip Only)",
}
_COMBINE_LABEL = {
    "예": "① 예 (구매평 결합 진행)",
    "아니오": "② 아니오 (미적용)",
}
_URGENT_LABEL = {
    "예": "① 예 (긴급 진행)",
    "아니오": "② 아니오 (미적용)",
}


def _unwrap_sdt_with_text(sdt, text: str) -> None:
    """드롭다운 위젯(w:sdt)을 ★제거하고 선택 텍스트 run 으로 대체한다.

    방식: sdtContent 안의 플레이스홀더("클릭하여 선택") run 텍스트를 선택값으로 바꾼 뒤,
    sdtContent 의 자식(run·permStart/End 등)을 부모 문단의 sdt 자리로 그대로 옮기고
    sdt 래퍼를 삭제한다. perm 마커는 짝을 유지(문서 무결성)."""
    from docx.oxml.ns import qn  # 지연 import — docx 생성 시점에만 필요.

    content = sdt.find(qn("w:sdtContent"))
    if content is None:
        return
    t_elems = content.findall(".//" + qn("w:t"))
    if t_elems:
        t_elems[0].text = text          # 플레이스홀더 → 선택값
        for t in t_elems[1:]:
            t.text = ""                  # 잔재 제거
    parent = sdt.getparent()
    insert_at = parent.index(sdt)
    for child in list(content):          # sdtContent 자식을 sdt 자리로 이동(순서 보존)
        parent.insert(insert_at, child)
        insert_at += 1
    parent.remove(sdt)                   # 위젯 래퍼 삭제


def _fill_step1_dropdowns(doc, data: RevuFormData) -> None:
    """표0 r1 의 드롭다운 3개(콘텐츠 타입·구매평 결합·긴급 진행)만 텍스트로 치환.

    ★정확히 그 3개만 타겟 — 표0 r1 셀 안의 w:sdt 만 순회하고, 각 sdt 의 listItem 값으로
    어떤 드롭다운인지 식별한다(다른 셀의 sdt·표12 등은 건드리지 않음)."""
    from docx.oxml.ns import qn  # 지연 import.

    try:
        tc = doc.tables[0].rows[1].cells[0]._tc
    except (IndexError, KeyError):
        return

    for sdt in tc.findall(".//" + qn("w:sdt")):
        values = " ".join(
            (li.get(qn("w:value")) or "") for li in sdt.findall(".//" + qn("w:listItem"))
        )
        if "Blog Only" in values or "Clip Only" in values:
            text = _CONTENT_LABEL.get(data.content_type)
        elif "구매평 결합 진행" in values:
            text = _COMBINE_LABEL.get(data.purchase_combine, _COMBINE_LABEL["아니오"])
        elif "긴급 진행" in values:
            text = _URGENT_LABEL.get(data.urgent, _URGENT_LABEL["아니오"])
        else:
            continue  # 식별 불가 sdt 는 안전하게 건너뜀(치환하지 않음).
        if text:
            _unwrap_sdt_with_text(sdt, text)


def fill_document(doc, data: RevuFormData) -> None:
    """로드된 Document 에 입력값을 채운다(in-place). 표12(사전안내문)는 건드리지 않는다.

    값이 비어 있는 항목은 양식 빈칸 그대로 둔다(없는 값을 만들어 넣지 않음)."""

    def put(t, r, p, text):
        para = _cell_para(doc, t, r, p)
        if para is not None:
            _set_para_text(para, text)

    # Step1 드롭다운 3개(콘텐츠 타입·구매평 결합·긴급 진행) — 위젯 제거 + 선택 텍스트 치환.
    # 라벨 run("2. 콘텐츠 타입 선택: " 등)은 그대로 두고, 드롭다운 자리에만 선택값을 박는다.
    _fill_step1_dropdowns(doc, data)

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
    # 제공수량 — 입력값에 수량 단위("2개" 등)까지 직접 포함하므로 원본의 "개"는 쓰지 않는다
    # (안 그러면 "...2개 개"로 중복됨). 입력값을 그대로 사용.
    if data.provide_qty:
        put(7, 0, 1, f"제공수량: {data.provide_qty}")

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
    try:
        from docx import Document  # ★지연 import — docx 생성 시점에만 python-docx 필요.
    except ImportError as e:
        raise ImportError(
            "python-docx 가 설치돼 있지 않아 docx 를 생성할 수 없습니다. "
            "`pip install python-docx` (requirements.txt 에 python-docx>=1.1 포함)."
        ) from e
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
