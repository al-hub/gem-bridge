# gem-bridge (v2 Core Architecture)

Google Gemini Mobile App과 로컬/원격 Git 리포지토리를 연결하는 모듈형 에이전트 브리지 시스템.
단일 스크립트 구조를 탈피하여 **Dispatcher-Executor 패턴**과 **Read/Write 물리적 분리 및 안전 가드레일**을 구축하였습니다.

---

## 🏗️ 아키텍처 개요

```
[Google Drive 작업 문서 감지]
           │
           ▼
     daemon_v2.py (경량 디스패처)
           │
           ▼
  core/intent_analyzer.py (자연어/커맨드 분석 및 Pydantic JSON 스키마 강제)
           │
           ├─► TaskType.READ  ──► core/executor_read.py  (Git Push 절대 금지, 소스 수집 및 [보고서] 생성)
           ├─► TaskType.WRITE ──► core/executor_write.py (보호 파일 가드레일, Diff 생성, Git Commit & Push)
           └─► TaskType.EXEC  ──► core/executor_exec.py  (안전 명령어 실행 및 [실행결과] 회신)
           │
           ▼
  core/repo_manager.py (Public Shallow Clone/Pull & 로컬 경로/권한 검증)
```

---

## 📦 모듈 구성

1. **`core/intent_analyzer.py`**:
   - 자연어 지시 및 명시적 커맨드(`!분석`, `!작업`, `!실행`) 파싱.
   - `gemini-3.6-flash` 모델 기반 정형 Pydantic JSON 스키마 강제.
   - **Default = READ 원칙**: 지시가 불명확하거나 파일 수정 파라미터가 누락된 경우 무조건 READ로 강등.

2. **`core/repo_manager.py`**:
   - 대상 저장소 확인 및 동적 준비.
   - **Public 저장소**: `~/workspace/repos/` 하위에 `--depth 1` shallow clone 및 pull 수행.
   - **Private/로컬 저장소**: `config.json` 매핑 검증 및 디렉토리 접근 권한 확인.

3. **`core/executor_read.py` (READ 전용)**:
   - **Git Push 및 파일 수정 절대 금지** (엄격한 Read-Only).
   - 저장소 트리 구조 및 핵심 소스코드 컨텍스트 수집.
   - `gemini-3.6-flash`를 통해 심층 마크다운 분석 보고서 생성.
   - Google Drive API를 통해 `[보고서] {제목}` 형태의 신규 Google Docs 문서로 업로드 회신.

4. **`core/executor_write.py` (WRITE 전용)**:
   - **보호 파일 덮어쓰기 방지 가드레일** (`README*`, `ARCHITECTURE*`, `credentials.json`, `token.json`, `.env*` 등).
   - 반영 전 변경 사항에 대한 Unified Diff 생성 및 로깅.
   - 파일 쓰기 후 `git add`, `git commit -m`, `git push` 자동 수행.

5. **`core/executor_exec.py` (EXEC 전용)**:
   - 시스템 위험 명령 차단 및 안전한 명령어 실행.
   - 결과(stdout/stderr)를 Google Docs `[실행결과] ...`로 회신.

6. **`daemon_v2.py`**:
   - Google Drive 폴링 및 메인 루프 (경량 디스패처).
   - 작업 완료 시 원본 문서 휴지통 이동.
   - 예외 발생 시 `[오류] ...` 문서를 Drive에 생성하고 로컬 `result.log`에 안전 기록하여 **크래시 방지**.

---

## 🚀 실행 방법

### 1. v2 데몬 실행 (기본 폴링 모드)
```bash
python3 daemon_v2.py
```

### 2. 1회 폴링 실행 (테스트 및 배치 모드)
```bash
python3 daemon_v2.py --once
```

### 3. 단위 및 통합 테스트 실행
```bash
python3 -m unittest discover -s tests -v
```

---

## 🔒 핵심 가드레일 규칙

1. **Default는 무조건 READ**: 지시 내용에 구체적인 파일 경로(`target_path`)나 코드(`content`)가 누락되어 모호한 경우, 임의로 코드를 수정하지 않고 분석 보고서(READ)로만 응답합니다.
2. **보호 파일 파괴 방지**: 리포지토리의 핵심 문서(`README.md`, `ARCHITECTURE.md` 등) 및 인증/설정 파일은 실수로 인한 덮어쓰기가 원천 차단됩니다.
3. **무중단 크래시 방지**: 네트워크 오류나 권한 예외 등 돌발 상황 발생 시에도 프로세스가 종료되지 않고 에러 로그를 남긴 후 다음 작업을 안전하게 처리합니다.

---

## ⚙️ 부팅 시 무인 자동 실행 안내
Windows 재부팅 후에도 터미널 실행 없이 완전히 자동으로 구동되는 2단계 자동 실행 아키텍처에 대한 상세 설명은 **[`docs/AUTO_STARTUP_GUIDE.md`](docs/AUTO_STARTUP_GUIDE.md)** 문서를 참고하세요.

