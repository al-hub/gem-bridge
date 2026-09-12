# gem-bridge 유지보수, 업데이트 및 삭제 가이드 (Maintenance & Uninstall Guide)

본 문서는 `gem-bridge` 시스템의 **버전 업데이트 방법, 설정 변경 반영, 문제 해결, 그리고 시스템 완전 삭제(Uninstallation) 절차**를 다룹니다.

---

## 1. 시스템 업데이트 방법 (Update Guide)

새로운 기능 추가나 버그 수정 패치가 배포되었을 때 최신 상태로 갱신하는 절차입니다.

### 1.1 최신 코드 반영
```bash
cd /home/al-hub/workspace/gem-bridge
git pull origin main
```

### 1.2 데몬 무중단 재기동 (최신 코드 로드)
systemd의 `Restart=always` 정책을 활용하여 무중단으로 최신 코드를 반영합니다.

**방법 A (일반 사용자 권한 - pkill 활용)**:
```bash
pkill -f "python3.*bridge_daemon.py"
# 10초 후 systemd가 새 코드로 데몬을 자동 부활시킵니다.
```

**방법 B (관리자 권한 - systemctl 활용)**:
```bash
sudo systemctl restart gem-bridge.service
```

### 1.3 업데이트 정상 반영 확인
1. **서비스 상태 확인**:
   ```bash
   systemctl status gem-bridge.service
   ```
2. **실행 로그 및 버전 확인**:
   ```bash
   tail -n 20 /home/al-hub/workspace/gem-bridge/result.log
   ```
   - 로그에 `=== gem-bridge v{버전} Dispatcher Daemon Started ===`가 정상적으로 표시되는지 확인합니다.

> ⚠️ **버전 업데이트 정책 준수 안내**:
> - 사용자의 명시적 허락 없이 **메이저 버전(v2 ➔ v3)은 업데이트되지 않습니다.**
> - 모든 업데이트는 마이너 및 패치(`v2.0.2`, `v2.1.0` 등) 범위 내에서만 안전하게 진행됩니다.

---

## 2. 설정 변경 및 관리 대상 저장소 추가/수정

### 2.1 관리 대상 리포지토리 추가/삭제
스마트폰에서 새로운 프로젝트를 다루려면 `config.json`에 경로를 등록합니다:

```json
{
  "poll_interval_seconds": 3,
  "repositories": {
    "gem-bridge": "/home/al-hub/workspace/gem-bridge",
    "새프로젝트": "/home/al-hub/workspace/새프로젝트"
  },
  "gemini_api_key": "AIzaSy..."
}
```

### 2.2 설정 반영
`config.json` 수정 후 데몬을 재시작합니다:
```bash
pkill -f "python3.*bridge_daemon.py"
```

---

## 3. 완전 삭제 및 서비스 제거 (Uninstall Guide)

더 이상 `gem-bridge`를 사용하지 않아 시스템에서 모든 구성 요소를 깨끗이 제거하고자 할 때 아래 4단계를 순서대로 진행합니다.

### 1단계: Linux systemd 백그라운드 서비스 해제 및 삭제
데몬 서비스를 영구 정지하고 systemd 등록을 해제합니다:
```bash
# 1. 서비스 정지
sudo systemctl stop gem-bridge.service

# 2. 부팅 시 자동 시작 해제
sudo systemctl disable gem-bridge.service

# 3. 서비스 등록 파일 삭제
sudo rm /etc/systemd/system/gem-bridge.service

# 4. systemd 데몬 리로드
sudo systemctl daemon-reload
sudo systemctl reset-failed
```

---

### 2단계: Windows 시작프로그램 자동 기동 스크립트 삭제
Windows 부팅 시 WSL을 깨우던 VBS 스크립트를 삭제합니다.

**WSL 터미널에서 바로 실행**:
```bash
rm "/mnt/c/Users/82108/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup/start_wsl_bridge.vbs"
```

**또는 Windows 탐색기에서 수동 삭제**:
- `Win + R` ➔ `shell:startup` 입력 후 폴더 내의 `start_wsl_bridge.vbs` 파일 삭제.

---

### 3단계: Google Drive 인증 토큰 및 API 키 파기
보안을 위해 로컬에 저장된 인증 정보와 API 접근 권한을 파기합니다:

1. **로컬 토큰 및 설정 삭제**:
   ```bash
   cd /home/al-hub/workspace/gem-bridge
   rm -f token.json credentials.json
   ```
2. **Google 계정에서 서드파티 액세스 권한 철회**:
   - [Google 계정 서드파티 앱 관리](https://myaccount.google.com/connections) 페이지 접속.
   - `gem-bridge` 또는 등록했던 데스크톱 애플리케이션의 Google Drive 접근 권한 삭제.

---

### 4단계: 리포지토리 및 캐시 데이터 정리
```bash
# 1. 자동 Clone된 퍼블릭 저장소 캐시 정리 (필요 시)
rm -rf ~/workspace/repos

# 2. gem-bridge 프로젝트 디렉토리 삭제 (필요 시)
cd ~
rm -rf /home/al-hub/workspace/gem-bridge
```

위 4단계를 마치면 컴퓨터에는 어떠한 백그라운드 프로세스나 권한도 남지 않고 완전 삭제됩니다.
