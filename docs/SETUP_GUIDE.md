# gem-bridge 사전 준비 및 설정 가이드 (Setup Guide)

본 문서는 `gem-bridge`를 구동하기 위해 사용자가 사전에 준비해야 하는 외부 서비스 권한, API 키, 로컬 설정 및 환경 구성을 단계별로 안내합니다.

---

## 1. 사전 준비 체크리스트

| 항목 | 목적 | 필요 파일 / 결과물 |
| :--- | :--- | :--- |
| **Google Cloud Console** | Google Drive API 접근 및 독스 문서 감시/회신 | `credentials.json`, `token.json` |
| **Google AI Studio** | `gemini-3.6-flash` 기반 인텐트 분석 및 보고서 작성 | `gemini_api_key` |
| **Git 인증 설정** | WRITE 작업 시 자동 commit & push 수행 | SSH 키 또는 Git Credential Helper |
| **로컬 환경 설정** | 데몬 감시 주기 및 대상 저장소 매핑 | `config.json` |
| **WSL2 & Windows** | PC 부팅 시 터미널 없이 무인 자동 실행 | `start_wsl_bridge.vbs`, `/etc/wsl.conf` |

---

## 2. 세부 설정 단계

### Step 1: Google Cloud Console 및 Drive API 설정
`gem-bridge`는 사용자의 Google Drive에 생성되는 문서를 감시하고, 분석 보고서를 신규 독스로 회신합니다.

1. [Google Cloud Console](https://console.cloud.google.com/)에 접속하여 새 프로젝트를 생성합니다.
2. **API 및 서비스 ➔ 라이브러리** 메뉴에서 **Google Drive API**를 검색하여 **사용(Enable)** 설정합니다.
3. **OAuth 동의 화면**:
   - 사용자 유형을 **외부(External)**로 선택합니다.
   - 테스트 사용자(Test Users) 목록에 본인의 Google 계정(스마트폰에서 사용하는 구글 드라이브 계정)을 추가합니다.
4. **사용자 인증 정보 만들기**:
   - **OAuth 클라이언트 ID** 생성 ➔ 애플리케이션 유형: **데스크톱 앱(Desktop App)** 선택.
   - 생성된 인증 정보 JSON 파일을 다운로드하여 리포지토리 루트에 **`credentials.json`** 이름으로 저장합니다.
5. **토큰 발급**:
   - 터미널에서 인증 헬퍼 스크립트를 실행합니다:
     ```bash
     python3 auth_helper.py
     ```
   - 콘솔에 표시되는 URL을 브라우저에 붙여넣어 구글 로그인 및 권한 승인을 완료합니다.
   - 리다이렉트된 URL(`http://localhost:8080/?state=...&code=...`)을 복사하여 터미널에 붙여넣으면 **`token.json`**이 자동 발급됩니다.

---

### Step 2: Google AI Studio Gemini API 키 발급
`gem-bridge`는 의도 분석 및 코드베이스 분석 보고서 생성에 **`gemini-3.6-flash`** 모델을 사용합니다.

1. [Google AI Studio](https://aistudio.google.com/)에 로그인합니다.
2. **Get API Key** 메뉴에서 새 API 키를 발급받습니다.
3. 발급받은 키를 복사해 둡니다.

---

### Step 3: `config.json` 설정
리포지토리 루트의 `config.json` 파일에 관리 대상 저장소 목록과 API 키를 구성합니다.

```json
{
  "poll_interval_seconds": 3,
  "repositories": {
    "gem-bridge": "/home/al-hub/workspace/gem-bridge",
    "my-app": "/home/al-hub/workspace/my-app"
  },
  "gemini_api_key": "AIzaSy..."
}
```

- **`poll_interval_seconds`**: Google Drive 감시 폴링 주기(초 단위, 권장: 3~5초).
- **`repositories`**: 스마트폰에서 작업 대상 리포지토리를 지칭할 때 매핑될 `{저장소_식별자: 로컬_절대경로}` 딕셔너리.
- **`gemini_api_key`**: Step 2에서 발급받은 Gemini API 키.
- **`protected_files`** (선택): WRITE 작업 시 덮어쓰기가 방지될 보호 패턴 목록 (미설정 시 기본 보호 패턴 적용).

---

### Step 4: Git Push 인증 구성 (WRITE 작업 필수)
`gem-bridge`가 WRITE 태스크 수행 후 리모트 저장소로 자동 `git push`를 성공하려면 Git 인증이 구성되어 있어야 합니다.

1. **SSH Key 권장**:
   ```bash
   ssh -T git@github.com
   # "Hi <username>! You've successfully authenticated..." 메시지 확인
   ```
2. **Public 저장소 Clone 기본 경로**:
   - 외부 URL(`https://github.com/...`)을 대상으로 한 요청은 `~/workspace/repos/` 디렉토리에 자동 복제됩니다.

---

### Step 5: WSL2 및 부팅 자동 실행 설정

1. **WSL systemd 활성화**:
   `/etc/wsl.conf`에 다음 항목이 포함되어 있는지 확인합니다:
   ```ini
   [boot]
   systemd=true
   ```
2. **systemd 서비스 등록**:
   [`/etc/systemd/system/gem-bridge.service`](file:///etc/systemd/system/gem-bridge.service) 등록 및 활성화:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable gem-bridge.service
   sudo systemctl start gem-bridge.service
   ```
3. **Windows 로그인 시 무인 자동 기동 (VBS 등록)**:
   - Windows 시작프로그램 경로(`C:\Users\<사용자>\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\start_wsl_bridge.vbs`)에 스크립트를 배치합니다.
   - 상세 구성법은 [AUTO_STARTUP_GUIDE.md](AUTO_STARTUP_GUIDE.md)를 참고하세요.

---

## 3. 설정 완료 검증

아래 명령어를 실행하여 20개 단위 테스트가 통과하는지 확인합니다:

```bash
python3 -m unittest discover -s tests -v
```

모든 테스트가 `OK`로 끝나면 모든 사전 준비가 정상적으로 완료된 것입니다.
