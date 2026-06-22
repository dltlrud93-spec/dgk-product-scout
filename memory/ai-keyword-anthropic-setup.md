---
name: ai-keyword-anthropic-setup
description: AI 키워드 자동완성(Claude) 배포 설정 — ANTHROPIC_API_KEY 시크릿·anthropic 의존성·재부팅
metadata:
  type: project
---

체험단 양식 Step4 "🤖 AI 키워드 자동완성"(2026-06 추가, src/core/keyword_ai.py)을 Streamlit Cloud 에서 쓰려면:

1. Streamlit Cloud → App settings → Secrets 에 `ANTHROPIC_API_KEY = "sk-..."` 추가. ★코드/git 금지(Public repo). 없으면 앱은 "AI로 키워드 생성" 버튼을 비활성화하고 안내만 띄움(죽지 않음).
2. requirements.txt 에 `anthropic>=0.40` 추가됨 → Cloud 가 의존성 재설치하도록 ★Reboot 필요([[test-real-render-path-applus-restart]] — 모듈/의존성 캐시).
3. 키 해석은 resolve_secret 단일 경로(app `_secret_candidates("ANTHROPIC_API_KEY")` → keyword_ai 에 api_key 주입). keyword_ai 는 순수 함수라 st.secrets 직접 안 봄. anthropic 은 ★지연 import([[lazy-import-optional-deps]]).
4. 모델/토큰은 config.CLAUDE_MODEL(claude-sonnet-4-6)/CLAUDE_MAX_TOKENS. 비용 절약 위해 (차종,제품) 입력 6시간 st.cache_data.

**How to apply:** AI 자동완성이 "키 필요" 안내만 뜨면 → Secrets 에 키 누락이거나 Reboot 안 한 것. [[always-push-after-work]] 와 함께 push+Reboot 확인.
