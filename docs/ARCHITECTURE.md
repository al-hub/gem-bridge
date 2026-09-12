# gem-bridge 상세 구축 가이드 및 트러블슈팅 (ARCHITECTURE.md)

## 1. 프로젝트 개요 및 목적
스마트폰의 Gemini 모바일 앱에서 `@Google Drive !` 형태의 자연어 한 줄 프롬프트 입력을 통해, WSL2 환경의 백그라운드 데몬을 거쳐 GitHub 원격 저장소에 자동으로 코드/문서를 커밋 및 푸시하는 완전 무인 자동화 파이프라인 구축.

---

## 2. 접근 방식 비교 및 선정 이유
* **로컬 동기화 폴더 직접 감시 시도**: Google Drive for Desktop을 통한 로컬 동기화 폴더 파일 변경 감시 방식을 초기 검토했으나, 생성되는 `.gdoc` 파일이 실제 본문 데이터가 아닌 단순 메타데이터 바로가기 링크(URL 및 ID)로 구성되어 있어 본문 텍스트 추출에 실패함.
* **Google Drive API v3 채택 (최종)**: 로컬 파일 감시 대신 Drive API v3를 직접 활용. `export_media(mimeType='text/plain')` API를 호출함으로써 구글 문서의 원문 텍스트를 손실 없이 온전하게 다운로드/파싱할 수 있도록 구조를 확립함.

---

## 3. 최종 기술 스택
* **실행 환경**: Windows 11 WSL2 (Ubuntu / Linux)
* **프로세스 관리**: systemd 상시 백그라운드 데몬 (5초 주기 폴링)
* **구글 연동**: Google Drive API v3 + OAuth 2.0 (`credentials.json`, `token.json`)
* **자연어 처리/파서**: Google GenAI SDK (`gemini-3.8-flash` 모델 기반 명령어 및 코드 추출)
* **형상 관리**: Git CLI (자동 커밋 및 푸시)

---

## 4. 재구축 단계 (Setup Guide)
1. **WSL2 systemd 활성화**:
   WSL2 내부의 `/etc/wsl.conf` 파일에 아래 설정을 추가한 뒤 Windows PowerShell에서 `wsl --shutdown`으로 완전히 재시작하여 systemd(PID 1) 활성화.
   ```ini
   [boot]
   systemd=true
   ```
2. **GCP 프로젝트 설정 및 OAuth 클라이언트 발급**:
   Google Cloud Console에서 프로젝트를 생성하고 Google Drive API를 활성화. Desktop App 타입의 OAuth 2.0 클라이언트 ID를 생성하여 `credentials.json` 파일을 다운로드 및 프로젝트 루트에 배치.
3. **OAuth 동의 화면 테스트 사용자 등록**:
   OAuth 동의 화면의 "Test users(테스트 사용자)" 목록에 연동에 사용할 본인 구글 계정을 등록(403 access_denied 방지).
4. **수동 리다이렉트 URL 방식으로 토큰 발급**:
   WSL2 환경 내 브라우저 리다이렉트 간섭 및 포트 바인딩 이슈를 피하기 위해, 인증 URL 접속 후 리다이렉트된 결과 URL(또는 인증 코드)을 터미널에 직접 붙여넣어 `token.json`을 생성.
5. **설정 파일 및 systemd 서비스 등록**:
   저장소 경로 및 설정을 담은 `config.json`을 작성하고, `/etc/systemd/system/gem-bridge.service` 유닛 파일을 등록한 뒤 서비스를 활성화/시작.
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now gem-bridge.service
   ```

---

## 5. 트러블슈팅 내역 (Troubleshooting Log)
| 문제 현상 / 한계 | 원인 분석 | 해결 방안 |
| :--- | :--- | :--- |
| `.gdoc` 메타데이터 한계 | 로컬 드라이브의 `.gdoc`은 문서 본문이 없는 JSON 메타데이터 바로가기 파일임. | Google Drive API v3의 `files().export_media(mimeType='text/plain')`를 호출하여 평문 텍스트 온전히 다운로드. |
| OAuth 403 access_denied | 게시 상태가 '테스트' 단계인 GCP 프로젝트에서 미승인 계정으로 로그인 시도. | GCP Console OAuth 동의 화면의 Test users에 해당 구글 계정 명시적 등록. |
| WSL2 브라우저 리다이렉트 / PKCE 오류 | WSL2 환경에서 로컬 웹서버 자동 리다이렉트 및 브라우저 세션 간섭으로 토큰 획득 실패. | 로컬 서버 방식 대신 인증 URL 접속 후 최종 리다이렉트 URL을 터미널 콘솔에 수동 입력하여 `token.json` 발급. |
| systemd 미지원 (PID 1 이슈) | 초기 WSL2 배포판에서 systemd가 비활성화되어 백그라운드 서비스 상시 구동 불가. | `/etc/wsl.conf`에 `[boot] systemd=true` 설정 추가 후 `wsl --shutdown` 재기동으로 systemd 활성화. |
| Drive API 쿼리 특수문자 누락 | Drive API 검색 쿼리에서 `contains '!'`와 같은 특수기호 필터링이 누락되거나 비정상 동작. | API 쿼리에서는 최근 수정 문서를 포괄 조회한 후, Python 데몬 단에서 `!`, `깃`, `task`, `작업` 등의 키워드를 정밀 검사. |
| LLM 모델 지원 종료 (404 Not Found) | 기존 구버전 모델(`gemini-2.5-flash`) 엔드포인트 지원 중단. | SDK 호출 코드를 최신 표준 모델인 `gemini-3.8-flash`로 전면 갱신하여 해결. |
| 수동 JSON 입력의 번거로움 | 모바일에서 작업 요청 시 엄격한 JSON 구조나 커밋 형식 입력의 불편함. | 데몬 내에 Gemini Flash 기반 자연어 파서를 통합하여 완전한 자연어 입력만으로도 타깃 파일 경로, 커밋 메시지, 코드 변경사항 자동 추출. |

---

## 6. 최종 동작 흐름 (Architecture Flow)
1. **모바일 프롬프트 입력**: 스마트폰 Gemini 앱에서 `@Google Drive ! [작업 내용]` 프롬프트 실행.
2. **구글 문서 자동 생성**: Gemini가 Google Drive에 요청 내용이 담긴 신규 문서를 생성.
3. **데몬 폴링 감지 (5초 주기)**: WSL2의 `gem-bridge` systemd 데몬이 신규 문서를 감지하고 `export_media`로 본문 추출.
4. **LLM 파싱**: `gemini-3.8-flash`가 본문 텍스트를 분석하여 대상 파일 경로, 수정/작성할 코드, Git 커밋 메시지를 자동 추출.
5. **Git 커밋 및 원격 푸시**: 로컬 작업 디렉터리에 반영 후 `git add`, `git commit`, `git push` 실행.
6. **문서 정리**: 작업 완료된 원본 구글 문서를 Google Drive 휴지통(Trash)으로 자동 이동하여 중복 실행 방지 및 정리.