# Gemini Mobile to Git Bridge 구축 여정 및 아키텍처

## 1. 목적 (Goal)
- **배경**: 스마트폰 Gemini 모바일 앱을 통해 이동 중에도 자연어 명령으로 로컬 WSL 환경을 거쳐 GitHub 저장소에 코드를 자동 반영하는 파이프라인 구축.
- **핵심 목표**: 수동 JSON 작성이나 PC 터미널 조작 없이, 모바일 프롬프트만으로 파일 생성/수정 및 git commit & push 완전 자동화.

## 2. 시도했던 적용 방식들 (Explored Approaches)
1. **로컬 파일시스템 직접 감시 (Local Polling)**: WSL 내부 및 Google Drive 데스크톱 동기화 폴더 직접 감시 시도. 그러나 구글 독스는 메타데이터 바로가기(.gdoc) 형태이므로 WSL에서 본문 내용을 직접 읽을 수 없어 한계 확인.
2. **Google Drive API v3 기반 폴링**: GCP Google Drive API v3를 활용해 클라우드 문서를 직접 조회하고 export_media(mimeType='text/plain')로 실제 평문 텍스트를 다운로드하는 방식으로 전환.

## 3. 최종 선택 방식 (Final Selected Stack)
- **Google Drive API v3 + OAuth 2.0 (Desktop App)**
- **Gemini Flash (LLM Parser Fallback)**: 사용자가 JSON이 아닌 자연어로 작성해도 LLM이 스스로 Git 작업 명세(repo, target_path, content, commit_message)를 생성.
- **systemd Daemon**: WSL2 백그라운드에서 5초 주기 상시 감시.
- **비용**: GCP Free Tier 및 Gemini API 무료 티어 내 완전 무료 구동.

## 4. 최종 구성 방법 (Setup Steps)
1. **GCP 프로젝트 구성**: Google Drive API 활성화, OAuth 동의 화면 및 테스트 사용자 등록, Desktop App 클라이언트 ID 생성(credentials.json).
2. **데몬 스크립트 작성(bridge_daemon.py)**: 감시 쿼리 확장((name contains '!' or name contains '깃' or name contains 'task' or name contains '작업')), 자연어 파서(google-genai) 추가, 로컬 Git 쓰기 및 자동 푸시, 구글 문서 휴지통 이동 로직 구현.
3. **systemd 서비스 등록**: /etc/wsl.conf에 [boot] systemd=true 설정 후 WSL 재시작, gem-bridge.service 등록 및 자동 시작 활성화.

## 5. 주요 문제점 및 해결 사항 (Troubleshooting)
- **문제 1**: 구글 독스 본문 미인식 -> export_media(mimeType='text/plain')로 평문 텍스트 다운로드 해결.
- **문제 2**: OAuth 403 오류(access_denied) -> GCP OAuth 대상 'Test users'에 계정 등록 해결.
- **문제 3**: WSL2 브라우저 포트 포워딩 간섭 및 verifier 누락 -> 1회용 인증 스크립트로 리다이렉트 URL 직접 입력받아 token.json 발급.
- **문제 4**: systemd 미지원 오류(PID 1) -> /etc/wsl.conf 설정 후 wsl --shutdown 재부팅으로 systemd 활성화.
- **문제 5**: 모바일 JSON 타이핑 번거로움 -> 느낌표(!) 트리거 도입 및 google-genai LLM 파서 결합으로 순수 자연어 지원.

## 6. 최종 동작 구조 (Architecture Flow)
모바일 Gemini 프롬프트(@Google Drive ! ...) -> Google Drive 구글 독스 생성 -> WSL 데몬 5초 주기 감시 감지 -> Gemini Flash로 자연어 파싱 및 마크다운 파일 생성 -> 로컬 Git commit & push -> 완료된 구글 문서 휴지통 이동.

## 7. 사용 방법 (Usage)
모바일 Gemini 앱에서 `@Google Drive ! 문서 만들자: [요청 사항]` 형태로 요청.