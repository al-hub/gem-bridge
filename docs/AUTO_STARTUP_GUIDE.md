# Windows 부팅 시 WSL 및 gem-bridge 데몬 무인 자동 실행 가이드

## 1. 개요 및 배경

Google Drive 기반 모바일-Git 자동화 브리지(`gem-bridge`)는 사용자가 스마트폰에서 구글 문서를 작성했을 때 **PC를 켜기만 하면 사용자 개입(터미널 실행 등) 없이도 백그라운드에서 상시 감시 및 처리**되어야 합니다.

그러나 **WSL2(Windows Subsystem for Linux 2)** 환경 특성상 다음과 같은 구조적 제약이 존재합니다:
- **WSL의 기본 동작 방식(Cold-start)**: Windows가 켜져도 사용자가 터미널(WSL, Ubuntu, VS Code 등)을 직접 열기 전까지는 WSL 가상머신 자체가 시작되지 않습니다.
- 따라서 WSL 내부의 `systemd` 서비스(`gem-bridge.service`)가 아무리 활성화(`enabled`)되어 있더라도, **Windows가 재부팅되면 사용자가 터미널을 한 번 켜주기 전까지 데몬이 동작하지 않는 문제**가 발생합니다.

이를 해결하기 위해 **2단계 무인 자동 실행 파이프라인**을 구축하여 완전 자동화를 달성했습니다.

---

## 2. 2단계 무인 자동 실행 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│ 1단계: Windows 부팅 및 사용자 로그인                        │
│   └─► Windows [시작프로그램] 폴더                           │
│         └─► start_wsl_bridge.vbs 실행                       │
│               └─► wsl.exe --exec /bin/true (화면 없이 백그라운드 기동)│
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 2단계: WSL2 커널 및 systemd 부팅                            │
│   └─► /etc/wsl.conf ([boot] systemd=true)                   │
│         └─► /etc/systemd/system/gem-bridge.service 자동 실행│
│               └─► daemon_v2.py (gem-bridge v2 Dispatcher) 구동│
│                     └─► Google Drive 상시 감시 시작         │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 구성 요소 및 설정 상세

### (1) Windows 시작프로그램 VBS 스크립트
- **파일 경로**:
  `C:\Users\82108\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\start_wsl_bridge.vbs`
  (WSL 내 경로: `/mnt/c/Users/82108/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup/start_wsl_bridge.vbs`)
- **스크립트 내용**:
  ```vbs
  Set WshShell = CreateObject("WScript.Shell")
  WshShell.Run "wsl.exe --exec /bin/true", 0, False
  ```
- **주요 파라미터 설명**:
  - `wsl.exe --exec /bin/true`: 콘솔 셸을 띄우지 않고 가장 가벼운 `/bin/true` 명령만 수행하여 WSL VM을 즉시 부팅시킵니다.
  - `0`: 창 숨김(Hidden) 플래그로, 윈도우 로그인 시 검은 CMD 창이 일체 깜빡이지 않습니다.
  - `False`: 스크립트 완료를 기다리지 않고 비동기 실행 후 VBS 프로세스가 즉시 종료됩니다.

---

### (2) WSL 부팅 시 systemd 활성화 (`/etc/wsl.conf`)
WSL이 기동될 때 Linux 표준 서비스 관리자인 `systemd`가 함께 시작되도록 설정되어 있습니다:
```ini
# /etc/wsl.conf
[boot]
systemd=true
```

---

### (3) systemd 서비스 (`gem-bridge.service`)
- **서비스 파일 경로**: `/etc/systemd/system/gem-bridge.service`
- **설정 내용**:
  ```ini
  [Unit]
  Description=Gemini Mobile to Git Bridge Daemon
  After=network.target

  [Service]
  Type=simple
  User=al-hub
  WorkingDirectory=/home/al-hub/workspace/gem-bridge
  ExecStart=/usr/bin/python3 /home/al-hub/workspace/gem-bridge/bridge_daemon.py
  Restart=always
  RestartSec=10

  [Install]
  WantedBy=multi-user.target
  ```
- **동작 특징**:
  - `Restart=always`: 예기치 못한 원인으로 프로세스가 다운되더라도 10초 내 자동 부활합니다.
  - `bridge_daemon.py`는 신규 v2 엔진인 `daemon_v2.py`의 `main()` 함수로 위임(포워딩)되어 안전 가드레일이 항상 보장됩니다.

---

## 4. 동작 확인 및 관리 방법

### 1) 서비스 상태 확인 (WSL 터미널)
```bash
systemctl status gem-bridge.service
```

### 2) 실시간 로그 모니터링
```bash
tail -f /home/al-hub/workspace/gem-bridge/result.log
```

### 3) 서비스 재시작
```bash
pkill -f "python3.*bridge_daemon.py"
# (systemd의 Restart=always 정책에 의해 10초 후 자동 재시작됨)
```

---

## 5. 결론 및 보장성

위 구성이 모두 적용 완료되어 있으므로, 사용자는:
1. **터미널을 열 필요가 없습니다.**
2. **별도 명령어를 실행할 필요가 없습니다.**
3. **컴퓨터 전원을 켜고 Windows에 로그인하기만 하면** 백그라운드에서 gem-bridge v2 시스템이 상시 작동합니다.
