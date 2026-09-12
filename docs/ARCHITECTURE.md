# gem-bridge v2 시스템 아키텍처 명세서 (Architecture Reference)

## 1. 개요 및 설계 철학

`gem-bridge`는 스마트폰의 **Google Gemini 모바일 앱** 및 **Google Docs**와 개발자의 로컬/원격 Git 저장소를 유기적으로 연결하는 자동화 브리지 에이전트 시스템입니다.

### 1.1 해결하고자 한 문제 (v1의 한계)
- **단일 스크립트 결합도**: 기존 `bridge_daemon.py`는 감시, 파싱, 파일 쓰기, Git Push가 단일 루프에 혼재되어 유지보수와 확장이 어려웠습니다.
- **의도치 않은 파일 파괴**: 자연어 지시가 모호할 때도 임의로 파일을 덮어쓰거나 불필요한 Git Push가 발생하는 치명적인 위험이 존재했습니다.
- **Cold-start 제약**: PC 재부팅 시 사용자가 WSL 터미널을 열기 전까지 데몬이 실행되지 않는 반쪽짜리 자동화였습니다.

### 1.2 v2 핵심 설계 원칙
1. **Dispatcher-Executor 패턴**:
   - 디스패처(`daemon_v2.py`)는 오직 작업 감지 및 라우팅만 담당하고, 실제 처리는 전용 실행기(Executor)로 위임합니다.
2. **Deep Module 원칙 (Michael Feathers의 Seam 원칙)**:
   - 각 모듈은 최소한의 인터페이스(Small Surface Area) 뒤에 풍부한 구현(Deep Implementation)을 은닉하여 호출자의 복잡도를 낮춥니다.
3. **Read / Write의 물리적 분리 및 안전 격리**:
   - 조회/분석(READ) 모듈에는 `git push` 및 파일 수정 코드가 물리적으로 일체 존재하지 않습니다.
4. **Default = READ 및 안전 가드레일**:
   - 지시가 모호하거나 파일 수정 파라미터가 불완전하면 시스템은 무조건 안전한 분석 보고서(READ)로 폴백합니다.

---

## 2. 전체 시스템 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             Google Drive API                                │
│   [사용자 모바일 지시 문서]                       [회신: 독스 보고서/결과]    │
└───────────────────────┬─────────────────────────────────────▲───────────────┘
                        │ (Polling / Export)                  │ (Create Docs)
                        ▼                                     │
┌─────────────────────────────────────────────────────────────┼───────────────┐
│ daemon_v2.py (경량 디스패처)                                 │               │
│   - Drive 감시 및 시스템 접두어 필터링                     │               │
│   - 완료 시 원본 문서 휴지통 이동                           │               │
│   - 에러 발생 시 크래시 방지 및 [오류] 문서 생성 ───────────┘               │
└───────────────────────┬─────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ core/intent_analyzer.py (IntentAnalyzer)                                    │
│   - 커맨드 접두어 분석 (!분석, !작업, !실행)                                 │
│   - gemini-3.6-flash 모델 기반 정형 Pydantic JSON 스키마 강제                │
│   - Default = READ 강등 가드레일                                            │
└───────────┬──────────────────────────┬──────────────────────────┬───────────┘
            │ TaskType.READ            │ TaskType.WRITE           │ TaskType.EXEC
            ▼                          ▼                          ▼
┌───────────────────────┐  ┌───────────────────────┐  ┌───────────────────────┐
│ core/executor_read.py │  │core/executor_write.py │  │ core/executor_exec.py │
│ - Git Push 절대 금지  │  │ - 보호 파일 차단      │  │ - 위험 커맨드 차단    │
│ - 소스 컨텍스트 수집  │  │ - Unified Diff 생성   │  │ - 샌드박스 실행       │
│ - Gemini 심층 보고서  │  │ - 파일 쓰기 및 커밋   │  │ - [실행결과] 회신     │
│ - [보고서] 독스 생성 ──► │ - Git Push 수행 ─────►│  │ ─────────────────────►│
└───────────┬───────────┘  └───────────┬───────────┘  └───────────┬───────────┘
            └──────────────────────────┼──────────────────────────┘
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ core/repo_manager.py (RepoManager)                                          │
│   - Public Git URL: ~/workspace/repos/ 하위 shallow clone/pull (--depth 1)  │
│   - Local Repository: config.json 매핑 검증 및 디렉토리/파일 권한 확인       │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 핵심 컴포넌트 상세 명세

### 3.1 `core/intent_analyzer.py`
- **역할**: 입력된 비정형 문서 텍스트로부터 작업 유형(`TaskType`)과 대상 저장소(`TargetRepo`)를 정확히 도출합니다.
- **인터페이스**:
  ```python
  def analyze(raw_text: str, title: str = "", available_repos: Optional[List[str]] = None) -> IntentAnalysisResult
  ```
- **데이터 모델 (`IntentAnalysisResult`)**:
  - `task_type: TaskType` (`READ`, `WRITE`, `EXEC`) - 기본값: `READ`
  - `target_repo: str` (로컬 식별자 또는 퍼블릭 Git URL)
  - `summary: str` (요청 요약)
  - `query: Optional[str]` (READ 질문/주제)
  - `target_path: Optional[str]` (WRITE 대상 파일)
  - `content: Optional[str]` (WRITE 대상 내용)
  - `commit_message: Optional[str]` (Git 커밋 메시지)
  - `exec_command: Optional[str]` (EXEC 커맨드)

### 3.2 `core/repo_manager.py`
- **역할**: 동적으로 작업 대상 리포지토리를 판별하고 안전하게 준비합니다.
- **인터페이스**:
  ```python
  def prepare_repo(target_repo: str) -> Path
  ```
- **동작 분기**:
  - **Public 저장소**: URL 정규식 감지 시 `~/workspace/repos/<repo_name>`에 `--depth 1` shallow clone 수행 (이미 존재할 경우 shallow pull 업데이트).
  - **Local 저장소**: `config.json`의 `repositories` 매핑 검증, 경로 존재 유무 및 파일 읽기/쓰기 권한 검사. 미지정 시 기본 저장소(`gem-bridge`)로 안전 폴백.

### 3.3 `core/executor_read.py` (READ 전용)
- **역할**: 코드베이스 컨텍스트를 분석하여 심층 보고서를 생성하고 구글 드라이브에 독스로 업로드합니다.
- **보안성**: `git push` 및 파일 쓰기 권한을 물리적으로 원천 차단한 Read-Only 컴포넌트입니다.
- **보고서 생성 파이프라인**:
  1. 리포지토리 디렉토리 트리 및 대상 소스코드 파일 추출 (최대 크기 안전 제한 적용).
  2. `gemini-3.6-flash`로 4단계 구조화 보고서(개요, 아키텍처 분석, 상세 발견점, 개선 제안) 생성.
  3. Google Drive API의 `MediaInMemoryUpload`를 통해 `[보고서] {제목}` 형태의 네이티브 Google Docs로 업로드.

### 3.4 `core/executor_write.py` (WRITE 전용)
- **역할**: 소스코드 파일을 안전하게 생성/수정하고 Git 커밋 및 푸시를 수행합니다.
- **가드레일 메커니즘**:
  1. **디렉토리 탈출 방지**: `target_path`가 리포지토리 루트를 벗어나지 못하도록 `Path.is_relative_to` 검증.
  2. **보호 파일 덮어쓰기 방지**: `README*`, `ARCHITECTURE*`, `credentials.json`, `token.json`, `.env*` 등의 파일이 이미 존재할 경우 `ProtectedFileError`를 발생시켜 파괴적 수정을 차단.
  3. **Unified Diff 감사 로깅**: 변경 전 파일과 신규 내용 간의 차이점을 `difflib`으로 계산하여 로그에 보존.
  4. **Git 자동화**: `git add`, `git commit -m`, `git push` 순차 실행 및 최신 커밋 해시 반환.

### 3.5 `core/executor_exec.py` (EXEC 전용)
- **역할**: 테스트 및 명령어 안전 실행.
- **가드레일**: `rm -rf /`, `mkfs`, 포크폭탄 등 치명적 명령어를 패턴 매칭으로 차단하고, 타임아웃(기본 60초) 내에서만 실행.

### 3.6 `daemon_v2.py` (디스패처 및 크래시 방지)
- **역할**: 구글 드라이브를 3~5초 간격으로 폴링하여 작업을 감지하고 각 실행기로 디스패치합니다.
- **무중단 내결함성**:
  - 개별 태스크 처리 도중 예외가 발생하더라도 메인 루프가 종료되지 않습니다.
  - 예외 트레이스백을 구글 드라이브의 `[오류] {제목}` 문서로 즉시 업로드하여 모바일 사용자에게 알리고, 로컬 `result.log`에 안전하게 기록합니다.
  - 정상 처리된 문서는 구글 드라이브 휴지통으로 이동시켜 중복 실행을 방지합니다.

---

## 4. 무인 자동 기동 (WSL 2-Stage Auto-Startup)

Windows 호스트 재부팅 시에도 사용자가 터미널을 열지 않고 백그라운드에서 동작할 수 있도록 2단계 자동 실행을 구현했습니다:

1. **1단계 (Windows)**: 시작프로그램의 `start_wsl_bridge.vbs`가 창 없이 조용히 `wsl.exe --exec /bin/true`를 호출.
2. **2단계 (WSL)**: `/etc/wsl.conf`에 의해 `systemd`가 기동되면서 `/etc/systemd/system/gem-bridge.service`가 `daemon_v2.py`를 영구 구동.

> 상세 가이드는 [AUTO_STARTUP_GUIDE.md](AUTO_STARTUP_GUIDE.md)를 참고하세요.

---

## 5. 버전 관리 및 변경 정책

- **Single Source of Truth**: [`core/__version__.py`](../core/__version__.py) 및 [`VERSION`](../VERSION)
- **Major Version Lock**: 메이저 버전(`v2.x.x` ➔ `v3.0.0`)은 사용자의 명시적 허락이 있기 전까지 엄격히 동결됩니다.
- **마이너/패치 정책**: 향후 기능 개선 및 버그 수정은 Minor / Patch 단위(`v2.0.2`, `v2.1.0` 등)로만 점진적 릴리스됩니다.