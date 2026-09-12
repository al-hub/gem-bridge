# gem-bridge 상세 구축 가이드 및 트러블슈팅 (ARCHITECTURE.md)

## 1. 목적
스마트폰 Gemini 모바일에서 자연어 한 줄(`@Google Drive !`)로 WSL2를 거쳐 GitHub 원격 저장소에 자동 커밋/푸시하는 무인 파이프라인 구축.

---

## 2. 접근 방식 비교
* **로컬 동기화 폴더 직접 감시 시도**: `.gdoc`이 메타데이터 바로가기여서 본문 추출 실패.
* **Google Drive API v3 채택**: `export_media(mimeType='text/plain')`로 원문 텍스트 온전히 다운로드.

---

## 3. 최종 기술 스택
* **운영체제 및 환경**: Windows 11 WSL2
* **프로세스 관리**: systemd 상시 데몬 (5초 폴링)
* **API 연동 및 인증**: Google Drive API v3 + OAuth 2.0 (`token.json`)
* **자연어 처리 / 파서**: google-genai SDK (`gemini-3.8-flash` LLM 파서)
* **버전 관리**: Git CLI

---

## 4. 재구축 단계 (Setup Guide)
1. **WSL2 설정**: `/etc/wsl.conf`에 `[boot] systemd=true` 설정 후 PowerShell에서 `wsl --shutdown` 재부팅.
2. **GCP 프로젝트 설정**: GCP 프로젝트 생성, Drive API 활성화, Desktop App OAuth 클라이언트 발급 (`credentials.json`).
3. **사용자 등록**: OAuth 동의 화면 Test users에 계정 등록.
4. **토큰 발급**: 수동 리다이렉트 URL 입력 방식으로 WSL에서 `token.json` 발급.
5. **데몬 등록**: `config.json` 및 `/etc/systemd/system/gem-bridge.service` 등록 후 데몬 활성화.

---

## 5. 전체 트러블슈팅 내역

| 문제 현상 | 해결 방안 |
| --- | --- |
| `.gdoc` 메타데이터 한계 | `export_media` 평문 다운로드로 해결. |
| OAuth 403 `access_denied` | GCP 콘솔 Test users 등록으로 해결. |
| WSL2 브라우저 리다이렉트 간섭/PKCE 오류 | 터미널 수동 URL 입력으로 해결. |
| systemd 미지원(PID 1) | `wsl.conf` 설정 후 재기동으로 해결. |
| Drive API 쿼리 특수문자 누락 | API 단독 contains '!' 대신 최근 문서 가져와 Python 단에서 `!`, `깃`, `task`, `작업` 키워드 직접 검사로 해결. |
| LLM 모델 지원 종료(404) | `gemini-2.5-flash`에서 `gemini-3.8-flash`로 갱신하여 해결. |
| 수동 JSON 입력 불편 | LLM(Gemini Flash) 파서를 데몬에 내장해 순수 자연어 지원. |

---

## 6. 동작 흐름
모바일 프롬프트 → Drive 문서 생성 → 데몬 5초 감지 → LLM 파싱 → Git 커밋/푸시 → Drive 문서 휴지통 자동 이동.