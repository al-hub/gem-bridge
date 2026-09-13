# gem-bridge (v2.1.21 Core Architecture)

> **스마트폰의 Google Gemini 모바일 앱 및 Google Tasks/Docs와 로컬/원격 Git 저장소를 유기적으로 연결하는 전 시나리오 공용 모바일 심리스(UMSP) 에이전트 브리지 시스템**

단일 스크립트 구조를 탈피하여 **Dispatcher-Executor 패턴**과 **Read/Write 물리적 분리 및 안전 가드레일**을 완벽하게 구축한 v2 아키텍처입니다.

---

## 🎯 개발 목적 및 핵심 가치

1. **모바일 개발 워크플로우 실현**:
   - 이동 중이나 침대 위에서 스마트폰으로 구글 문서에 아이디어나 분석 요청을 적으면, PC 데몬이 이를 감지하여 소스코드를 분석해 보고서를 작성하거나 코드를 수정하고 GitHub에 Push합니다.
2. **Read/Write 물리적 격리 및 코드 파괴 방지**:
   - 조회/분석(`READ`) 시에는 `git push`나 파일 수정을 절대 하지 않습니다.
   - 명시적인 커맨드가 없거나 모호한 요청은 **무조건 안전한 분석 보고서(READ)로만 응답**합니다.
3. **100% 무인 상시 가동 (WSL2 부팅 연동)**:
   - Windows 재부팅 후에도 사용자가 터미널을 열 필요 없이, 백그라운드에서 조용히 자동 구동됩니다.

---

## 🏗️ 시스템 아키텍처 개요

```
┌──────────────────────────────────────────────────────────────┐
│                    Google Drive API                          │
│   [모바일 지시 문서] ──► daemon_v2.py ──► [회신: 독스 보고서]  │
└──────────────────────────────┬───────────────────────────────┘
                               ▼
                    core/intent_analyzer.py
              (gemini-3.6-flash Pydantic JSON 강제)
                               │
       ┌───────────────────────┼───────────────────────┐
       ▼                       ▼                       ▼
  TaskType.READ           TaskType.WRITE          TaskType.EXEC
┌─────────────────────┐ ┌─────────────────────┐ ┌─────────────────────┐
│core/executor_read.py│ │core/executor_write.py││core/executor_exec.py│
│ - Git Push 절대 금지│ │ - 보호 파일 차단    │ │ - 위험 커맨드 차단  │
│ - 소스 컨텍스트 수집│ │ - Unified Diff 생성 │ │ - 안전한 쉘 실행    │
│ - [보고서] 독스 생성│ │ - Git commit & push │ │ - [실행결과] 회신   │
└─────────────────────┘ └─────────────────────┘ └─────────────────────┘
                               │
                               ▼
                      core/repo_manager.py
    (Public Shallow Clone ~/workspace/repos/ & 로컬 매핑 검증)
```

---

## 📚 전체 공식 문서 맵 (Documentation Index)

프로젝트를 원활하게 이해하고 운영하기 위해 아래의 상세 문서들을 제공합니다:

| 문서명 | 주요 내용 | 대상 독자 |
| :--- | :--- | :--- |
| **[`docs/SETUP_GUIDE.md`](docs/SETUP_GUIDE.md)** | • Google Cloud OAuth (`credentials.json`, `token.json`) 설정<br>• Gemini API Key 발급 및 `config.json` 매핑<br>• SSH 및 Git Push 인증 구성 | **설치 및 사전 준비** |
| **[`docs/USAGE_GUIDE.md`](docs/USAGE_GUIDE.md)** | • 모바일 구글 제미나이 앱 및 독스 활용법<br>• 커맨드 치트시트 (`!분석`, `!작업`, `!실행`)<br>• 프롬프트 작성 팁 및 결과 확인법 | **일상 사용 및 모바일 운용** |
| **[`docs/MAINTENANCE_AND_UNINSTALL_GUIDE.md`](docs/MAINTENANCE_AND_UNINSTALL_GUIDE.md)** | • 최신 패치 업데이트 방법 (`git pull` & 데몬 재기동)<br>• 관리 대상 저장소 추가/수정<br>• 4단계 시스템 완전 삭제(Uninstall) 절차 | **유지보수, 업데이트 및 삭제** |
| **[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** | • Dispatcher-Executor 패턴 심층 설계<br>• Seam 원칙 및 모듈 인터페이스 명세<br>• 가드레일 및 오류 격리 메커니즘 | **기술 동작 원리 / 아키텍처** |
| **[`docs/AUTO_STARTUP_GUIDE.md`](docs/AUTO_STARTUP_GUIDE.md)** | • Windows-WSL2 2단계 무인 자동 실행 원리<br>• VBS 스크립트 및 `systemd` 서비스 구성<br>• 무중단 상시 구동 트러블슈팅 | **부팅 자동화 원리 / 운영** |
| **[`CHANGELOG.md`](CHANGELOG.md)** | • 릴리스 히스토리 (v2.0.1, v2.0.0, v1.0.0)<br>• **Major Version Lock (v2 고정) 정책** 명시 | **버전 이력 관리** |


---

## ⚡ 빠른 시작 (Quick Start)

### 1. 필수 사전 준비
1. [SETUP_GUIDE.md](docs/SETUP_GUIDE.md)를 참고하여 `credentials.json`, `token.json`, `config.json`을 준비합니다.
2. 테스트 스위트 실행을 통해 환경 검증:
   ```bash
   python3 -m unittest discover -s tests -v
   ```

### 2. 데몬 실행 방법
```bash
# 기본 상시 백그라운드 데몬 실행
python3 daemon_v2.py

# 1회 폴링 실행 (디버깅 및 단발 테스트용)
python3 daemon_v2.py --once
```

### 3. 모바일 지시 테스트
스마트폰 구글 문서 앱에서 제목을 **`!분석 gem-bridge 구조 요약해줘`** 로 설정하고 본문에 질문을 적어 저장하면, 잠시 후 Google Drive에 **`[보고서] gem-bridge 구조 요약해줘`** 문서가 자동으로 생성됩니다.

---

## 🔒 핵심 가드레일 3원칙

1. **Default 무조건 READ**:
   - 지시가 모호하거나 파일 수정 필수값(`target_path`, `content`)이 누락된 경우 파일 수정 없이 안전하게 분석 보고서(`READ`)로만 처리합니다.
2. **보호 파일 파괴 차단**:
   - `README.md`, `ARCHITECTURE.md`, `credentials.json`, `token.json`, `.env` 등의 파일은 덮어쓰기가 원천 차단됩니다.
3. **무중단 내결함성 (Crash-Free Loop)**:
   - 돌발 예외가 발생하더라도 데몬이 다운되지 않으며, `[오류] ...` 독스를 생성하여 모바일로 오류 원인을 알려줍니다.

---

## 🏷️ 버전 관리 정책

- **현재 버전**: `v2.1.17` ([`core/__version__.py`](core/__version__.py), [`VERSION`](VERSION))
- **버전 정책**: `v2.1` 고정 (Strict Versioning Rule: Major/Minor 고정, 3번째 자리 z만 업데이트 `v2.1.z`)
