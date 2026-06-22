---
name: url-log-sheet-setup
description: 추적 URL 이력 적재용 구글 시트 설정 — url_log_sheet_id 시크릿 + 서비스계정 공유
metadata:
  type: project
---

체험단 양식 Step5 "📝 이 URL 이력 저장 (구글시트)"(2026-06 추가, src/core/url_log.py)를 쓰려면:

1. 이력용 구글 시트를 새로 만들고(아무 빈 시트, 헤더는 첫 저장 시 자동 생성), 그 ID(URL `/d/<여기>/edit`)를 Streamlit Secrets 에 `url_log_sheet_id = "..."` 로 추가(env `URL_LOG_SHEET_ID` 도 가능).
2. ★그 시트를 [[jogyeonpyo-sheet-connection]] 의 ★서비스계정 이메일에 ★편집자(쓰기)로 공유. 안 하면 append 가 권한오류 → 버튼이 "이력 저장 실패" 경고만 띄움(URL 생성은 정상).
3. 인증은 jogyeonpyo._authorize 재사용(gcp_service_account). 새 의존성 없음.

**How to apply:** "로그 시트 ID 미설정" 경고 → Secrets 에 url_log_sheet_id 없음(+Reboot). "이력 저장 실패" → 시트를 서비스계정에 편집자 공유 안 함. 저장은 saved/duplicate/no_config/error 만 반환하고 ★절대 URL 생성을 막지 않음(실패 무해 설계).
