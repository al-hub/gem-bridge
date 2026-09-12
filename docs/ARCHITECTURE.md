# [보고서] gem-bridge의 core/intent_analyzer.py 모듈의 역할과 JSON 스키마 강제 방식에 대해 상세히 분석해줘.

---

## 1. 개요 및 목적 (Executive Summary)

`gem-bridge` 프로젝트는 Google Drive 감시 데몬(`daemon_v2.py`)을 통해 수신된 자연어 문서나 커맨드 형태의 요청을 로컬 Git 저장소 작업으로 변환·수행하는 자동화 브리지 시스템입니다.

이 파이프라인의 핵심 진입점에 위치한 **`core/intent_analyzer.py`** 모듈은 사용자가 작성한 비정형 구글 문서 내용(제목 및 본문)을 분석하여 **시스템이 이해 가능한 정형화된 작업 객체(`IntentAnalysisResult`)로 변환**하는 역할을 담당합니다.

본 보고서에서는 `core/intent_analyzer.py` 모듈의 주요 역할과 내부 동작 메커니즘, 그리고 **Pydantic과 Google Gemini API(`gemini-3.6-flash`)를 활용한 JSON 스키마 강제 및 검증 방식**에 대해 체계적으로 분석합니다.

---

## 2. 코드베이스 구조 및 주요 컴포넌트 분석

### 2.1 아키텍처 상의 위치
`daemon_v2.py` 데몬 프로세스는 구글 드라이브에서 신규 작업 문서를 감지한 후 `IntentAnalyzer`를 호출합니다. 분석 결과 생성된 `IntentAnalysisResult` 객체의 `task_type`에 따라 각 전용 Executor(`ReadExecutor`, `WriteExecutor`, `ExecExecutor`)로 작업을 분기(Dispatch)합니다.

```
[Google Drive Task Doc]
          │
          ▼
   [daemon_v2.py]
          │
          ▼
┌──────────────────────────────────────────────┐
│ core/intent_analyzer.py (IntentAnalyzer)     │
│  - 1단계: Direct JSON 파싱                     │
│  - 2단계: 명령어 Prefix 감지 (!분석, !작업)   │
│  - 3단계: Gemini LLM 기반 구조화 분석         │
└──────────────────────────────────────────────┘
          │
          ▼ (IntentAnalysisResult)
┌──────────────────────────────────────────────┐
│ TaskType 분기                                │
│  ├─ READ  ──> ReadExecutor                   │
│  ├─ WRITE ──> WriteExecutor                  │
│  └─ EXEC  ──> ExecExecutor                   │
└──────────────────────────────────────────────┘
```

### 2.2 모듈 내 핵심 클래스 정의

#### 1) `TaskType` (Enum)
작업의 성격을 3가지 기본 타입으로 정의합니다. 안전성을 극대화하기 위해 기본값은 항상 `READ`로 처리됩니다.
- `READ`: 단순 조회, 코드베이스 분석, 질문 답변 (기본값)
- `WRITE`: 파일 생성/수정 및 Git 커밋
- `EXEC`: 쉘 명령 또는 테스트 실행

#### 2) `IntentAnalysisResult` (Pydantic BaseModel)
LLM 및 파서가 반환해야 하는 데이터 구조의 **단일 표준 진실 데이터 모델(Single Source of Truth)**입니다.

```python
# core/intent_analyzer.py
class TaskType(str, Enum):
    READ = "READ"
    WRITE = "WRITE"
    EXEC = "EXEC"


class IntentAnalysisResult(BaseModel):
    task_type: TaskType = Field(
        default=TaskType.READ,
        description="Task type: READ (for queries, analysis, inspections, default), WRITE (for modifying/creating files with git commit), EXEC (for running commands/tests). Default MUST be READ."
    )
    target_repo: str = Field(
        default="gem-bridge",
        description="Target repository name (from available repos) or public git clone URL (e.g., https://github.com/...)"
    )
    summary: str = Field(
        ...,
        description="Concise summary of the user's intent"
    )
    query: Optional[str] = Field(
        None,
        description="Analysis question or topic for READ tasks"
    )
    target_files_or_dirs: Optional[List[str]] = Field(
        default_factory=list,
        description="List of specific files or directories mentioned in the request"
    )
    target_path: Optional[str] = Field(
        None,
        description="Relative path of file to create or modify for WRITE tasks"
    )
    content: Optional[str] = Field(
        None,
        description="Full content of the file to write for WRITE tasks"
    )
    commit_message: Optional[str] = Field(
        None,
        description="Git commit message for WRITE tasks"
    )
    exec_command: Optional[str] = Field(
        None,
        description="Shell command to execute for EXEC tasks"
    )
    reasoning: Optional[str] = Field(
        None,
        description="Reasoning behind task type selection and extracted fields"
    )
```

---

## 3. 핵심 발견점 및 상세 답변 (Deep Dive)

### 3.1 `core/intent_analyzer.py` 모듈의 역할

`IntentAnalyzer` 클래스의 핵심 역할은 다음과 같이 4가지로 요약할 수 있습니다.

1. **비정형 자연어 명령의 구조화**: 사용자가 입력한 제목과 본문을 조합(`"제목: ... \n본문: ..."`)하여 의도를 파악합니다.
2. **다단계 하이브리드 의도 분석 (Hybrid Intent Parsing)**:
   - LLM 호출 비용 및 지연 시간을 줄이기 위해 **직접 JSON**, **명령어 Prefix(!분석, !작업, !실행)**, **LLM 분석** 순으로 3단계 파싱을 수행합니다.
3. **작업 대상 저장소 결정**: `config.json`에 정의된 사용 가능한 저장소 목록(`available_repos`) 중 어느 저장소를 대상으로 작업할지 추출하며, 기본값으로 `"gem-bridge"`를 할당합니다.
4. **결함 허용(Fault Tolerance) 및 Safe Fallback 보장**: LLM API 호출 실패, API Key 미설정, 혹은 JSON 파싱 오류 발생 시 시스템이 정지되지 않고 안전한 `READ` 타입의 Fallback 객체를 반환합니다.

```python
# core/intent_analyzer.py 일부
def analyze(
    self,
    raw_text: str,
    title: str = "",
    available_repos: Optional[List[str]] = None
) -> IntentAnalysisResult:
    available_repos = available_repos or [self.default_repo]
    combined_text = f"제목: {title}\n본문:\n{raw_text}".strip()

    # 1. Direct JSON payload 파싱 시도 (Legacy 또는 명시적 JSON 지원)
    json_payload = self._extract_raw_json(raw_text)
    if json_payload and isinstance(json_payload, dict):
        direct_result = self._parse_from_json_dict(json_payload, available_repos)
        if direct_result:
            return direct_result

    # 2. 명시적 명령어 Prefix 감지 (!분석, !작업, !실행 등)
    command_hint = self._detect_command_prefix(title, raw_text)

    # 3. Gemini LLM (gemini-3.6-flash) 구조화 분석
    if not self.client:
        logger.warning("Gemini Client not initialized... Falling back to default READ intent.")
        return self._build_fallback_read_intent(combined_text, available_repos, "Missing API Key")

    try:
        return self._analyze_with_llm(combined_text, available_repos, command_hint)
    except Exception as e:
        logger.error(f"Failed to analyze intent with LLM: {e}. Falling back to safe READ intent.")
        return self._build_fallback_read_intent(
            combined_text, available_repos, f"LLM parsing error fallback: {e}"
        )
```

---

### 3.2 JSON 스키마 강제 방식 (JSON Schema Enforcement)

`intent_analyzer.py`가 LLM의 환각(Hallucination)이나 형식이 맞지 않는 출력을 방지하고 **엄격한 JSON 스키마를 강제하는 방식**은 다음과 같은 4단계 레이어로 구현되어 있습니다.

#### Layer 1: Pydantic Schema를 통한 명확한 스키마 및 메타데이터 정의
- Pydantic의 `Field(description=...)` 메타데이터를 작성하여 필드별 요구사항과 힌트를 명시합니다.
- 예: `task_type` 필드 설명에 `"Default MUST be READ."` 및 `WRITE`, `EXEC`의 상세 조건을 명시함으로써 LLM 프롬프트 생성 시 자연스러운 제약조건을 제공합니다.

#### Layer 2: Prefix 감지를 통한 TaskType 힌트 튜닝 (`_detect_command_prefix`)
사용자가 문서 제목/본문 첫 줄에 `!분석`, `!작업`, `!실행` 등의 힌트를 제공한 경우, 이를 사전 감지하여 LLM 추론 단계에 힌트(`command_hint`)로 전달하여 정형화 분류의 정확도를 극대화합니다.

```python
# core/intent_analyzer.py
def _detect_command_prefix(self, title: str, text: str) -> Optional[TaskType]:
    check_str = f"{title}\n{text}".strip().lower()
    first_line = check_str.split("\n")[0].strip()

    if any(first_line.startswith(p) for p in ["!분석", "!analyze", "!조회", "!read", "!검토"]):
        return TaskType.READ
    if any(first_line.startswith(p) for p in ["!작업", "!write", "!수정", "!task"]):
        return TaskType.WRITE
    if any(first_line.startswith(p) for p in ["!실행", "!exec", "!run"]):
        return TaskType.EXEC
    return None
```

#### Layer 3: 구글 Gemini SDK (`google.genai`)의 Structured Output 및 JSON 전용 설정
- `google.genai` 신규 SDK의 `Client`를 사용하며, 최신 모델인 `gemini-3.6-flash`를 타겟팅합니다.
- `bridge_daemon.py` 및 `IntentAnalyzer` 내 LLM 호출 시 `config={"response_mime_type": "application/json"}` 옵션 또는 Pydantic 응답 스키마를 지정함으로써 모델이 Markdown Text가 아닌 **순수 JSON 문자열만 출력**하도록 API 수준에서 강제합니다.

#### Layer 4: Strict Parsing 및 Safe Fallback
- LLM 응답 JSON 문자열은 `IntentAnalysisResult.model_validate()` 또는 `json.loads()`를 거쳐 Pydantic 모델 인스턴스로 타입 검증을 받습니다.
- 만약 필드 타입 불일치, 필수 필드(`summary`) 누락, JSON 문법 오류 발생 시 예외(Exception)가 포착되고 `_build_fallback_read_intent()`가 실행됩니다.
- 이는 파생 작업으로 발생할 수 있는 원치 않는 파일 수정/삭제(`WRITE`)나 무단 명령어 실행(`EXEC`)을 차단하고, 가장 안전한 **기본 읽기 권한 작업(`READ`)으로 강제 강하(Fallback)**시키는 강력한 안전장치입니다.

---

## 4. 권장사항 및 개선점 (Recommendations & Next Steps)

1. **LLM Structured Output 설정 명시화 (`response_schema`)**:
   - 현재 `google.genai` SDK는 `config={"response_mime_type": "application/json", "response_schema": IntentAnalysisResult}`와 같이 Pydantic 클래스를 직접 전달하는 기능을 지원합니다. `_analyze_with_llm` 메서드 작성 시 해당 옵션을 명시적으로 적용하면 LLM이 스키마를 100% 준수하도록 API 단에서 보장할 수 있습니다.

2. **`target_path` 및 명령어에 대한 입력값 검증(Guardrails) 추가**:
   - `IntentAnalysisResult` 수준에서는 형식만 검증되므로, `WRITE` 태스크의 `target_path`에 대한 Path Traversal (예: `../../etc/passwd`) 검증 로직 또는 `EXEC` 태스크의 차단 명령어 목록(Blacklist) 검증을 `IntentAnalyzer` 검증 단계 직후 추가하는 것이 보안상 안전합니다.

3. **단위 테스트(Unit Test) 확충**:
   - `tests/test_intent_analyzer.py`에 올바른 JSON 파싱 외에도 잘못된 JSON, 알 수 없는 명령어 Prefix, LLM API failure 상황에서의 `READ` Fallback 정상 동작 여부를 검증하는 에지 케이스(Edge Case) 테스트를 보강하는 것을 권장합니다.