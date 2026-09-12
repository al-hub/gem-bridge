# gem-bridge 상세 구축 가이드 및 트러블슈팅 (ARCHITECTURE.md)

## 1. 프로젝트 개요 및 목적
스마트폰의 Gemini 모바일 앱에서 `@Google Drive !` 형태의 자연어 한 줄 프롬프트를 입력하면, WSL2 환경을 거쳐 GitHub 원격 저장소에 자동으로 커밋 및 푸시되는 완전 무인 자동화 파이프라인 구축.

---

## 2. 접근 방식 비교 및 선정 이유
* **로컬 동기화 폴더 직접 감시 시도**: Google Drive for Desktop을 통해 동기화된 로컬 폴더를 직접 감시하려 했으나, 동기화된 `.gdoc` 파일이 실제 본문 텍스트가 아닌 메타데이터 바로가기 형태(JSON 포맷의 링크 및 ID)로 저장되어 본문 추출 실패.
* **Google Drive API v3 채택 (최종)**: 로컬 파일 감시 대신 Drive API v3의 `export_media(mimeType='text/plain')` 엔드포인트를 호출하여 원문 텍스트를 손실 없이 온전하게 다운로드하는 구조로 전환.

---

## 3. 최종 기술 스택
* **실행 환경**: Windows 11 WSL2 (Ubuntu / Linux)
* **프로세스 관리**: systemd 상시 백그라운드 데몬 (5초 주기 폴링)
* **Google 연동**: Google Drive API v3 + OAuth 2.0 (`credentials.json`, `token.json`)
* **LLM 파서**: google-genai SDK (`gemini-3.8-flash` 모델 기반 명령어/코드/커밋 메시지 추출)
* **형상 관리**: Git CLI (원격 저장소 자동 커밋 및 푸시)

---

## 4. 재구축 단계 (Setup Guide)
1. **WSL2 systemd 활성화**:
   WSL2 내부 `/etc/wsl.conf` 파일에 아래 설정을 적용한 후, Windows PowerShell에서 `wsl --shutdown` 실행 후 재부팅하여 PID 1 systemd를 활성화.
   ```ini
   [boot]
   systemd=true
   ```
2. **GCP 프로젝트 설정 및 OAuth 클라이언트 발급**:
   Google Cloud Console에서 프로젝트 생성 후 Google Drive API 활성화. Desktop App(데스크톱 앱) 유형의 OAuth 2.0 클라이언트 ID를 생성하고 `credentials.json` 다운로드 후 프로젝트 루트에 배치.
3. **OAuth 동의 화면 Test users 등록**:
   GCP Console의 OAuth 동의 화면에서 'Test users(테스트 사용자)'에 연동 계정을 명시적으로 등록 (403 access_denied 사전 방지).
4. **수동 리다이렉트 URL 방식으로 토큰 발급**:
   WSL2 환경 내 브라우저 리다이렉트 간섭 및 PKCE 오류를 피하기 위해, 인증 URL 접속 후 브라우저 주소창의 리다이렉트된 URL(또는 인증 코드)을 복사하여 터미널에 직접 입력하는 방식으로 `token.json` 발급.
5. **설정 및 데몬 활성화**:
   `config.json` 구성 및 `/etc/systemd/system/gem-bridge.service` 유닛 등록 후 데몬 활성화 및 상시 구동.
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now gem-bridge.service
   ```

---

## 5. 전체 트러블슈팅 내역 (Troubleshooting Log)
| 문제 현상 / 한계 | 원인 분석 | 해결 방안 |
| --- | --- | --- |
| `.gdoc` 메타데이터 한계 | 로컬 드라이브의 `.gdoc`은 본문 데이터가 없는 JSON 메타데이터 바로가기임. | Google Drive API v3의 `export_media(mimeType='text/plain')`로 평문 텍스트 온전히 다운로드. |
| OAuth 403 access_denied | 테스트 상태인 GCP 앱에서 미승인 계정으로 접근 시 발생. | GCP Console OAuth 동의 화면의 Test users에 해당 계정 등록. |
| WSL2 브라우저 리다이렉트 간섭 / PKCE 오류 | WSL2 환경 내 로컬 포트 바인딩 및 브라우저 세션 리다이렉트 간섭 발생. | 자동 로컬 서버 대신 브라우저 접속 후 표시되는 최종 리다이렉트 URL을 터미널 콘솔에 수동 입력하여 해결. |
| systemd 미지원 (PID 1) | WSL2 기본 설정에서 systemd가 미활성화되어 데몬 상시 구동 불가. | `/etc/wsl.conf`에 `[boot] systemd=true` 설정 후 `wsl --shutdown` 재기동으로 해결. |
| Drive API 쿼리 특수문자 누락 | Drive API 단독 쿼리(`contains '!'`)에서 특수문자 검색이 누락되거나 비정상 필터링됨. | API로는 최근 문서를 포괄 조회한 후, Python 데몬 단에서 `!`, `깃`, `task`, `작업` 등의 키워드를 정밀 검사하도록 변경. |
| LLM 모델 지원 종료 (404) | 기존 사용하던 구버전 모델(`gemini-2.5-flash`) 엔드포인트 지원 종료. | 최신 모델인 `gemini-3.8-flash`로 SDK 호출 모델 갱신하여 해결. |
| 수동 JSON 입력의 불편함 | 모바일 환경에서 작업 지시 시 정형화된 JSON 입력이 비효율적임. | Gemini Flash 기반 자연어 파서를 데몬에 내장하여 자연어 프롬프트에서 타깃 파일, 코드, 커밋 메시지를 자동 추출하도록 개선. |

---

## 6. 최종 동작 흐름 (Architecture Flow)
1. **모바일 프롬프트**: 스마트폰 Gemini 모바일 앱에서 `@Google Drive ! [작업 내용]` 프롬프트 입력.
2. **Drive 문서 생성**: Google Drive에 요청 프롬프트가 담긴 문서가 자동으로 생성됨.
3. **데몬 5초 감지**: WSL2의 `gem-bridge` 상시 데몬이 5초 폴링을 통해 새 문서를 감지하고 `export_media`로 본문 추출.
4. **LLM 파싱**: `gemini-3.8-flash`가 본문 자연어를 파싱하여 대상 파일 경로, 수정/작성할 코드, 커밋 메시지 추출.
5. **Git 커밋/푸시**: 로컬 작업 디렉터리에 반영 후 Git CLI를 통해 `git add`, `git commit`, `git push` 자동 수행.
6. **문서 휴지통 자동 이동**: 작업이 완료된 Google Drive 원본 문서를 자동으로 휴지통으로 이동시켜 중복 실행 방지 및 정리.