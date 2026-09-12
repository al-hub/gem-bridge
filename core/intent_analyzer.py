import json
import logging
import re
import time
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

logger = logging.getLogger("gem_bridge.intent_analyzer")


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
    source_path: Optional[str] = Field(
        None,
        description="Original relative path if moving, renaming, or refactoring an existing file (e.g. 'index.html')"
    )
    target_path: Optional[str] = Field(
        None,
        description="Relative path of file to create or modify for WRITE tasks (e.g. 'docs/index.html')"
    )
    content: Optional[str] = Field(
        None,
        description="Full content of the file to write for WRITE tasks (if explicitly provided)"
    )
    instruction: Optional[str] = Field(
        None,
        description="Natural language instruction for modifying, refactoring, or updating the file when content is not provided directly"
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


class IntentAnalyzer:
    """
    Parses natural language instructions and explicit commands (!분석, !작업, !실행)
    using gemini-flash-latest with structured JSON schema enforcement.
    Default task type is strictly READ.
    """

    MODEL_NAME = "gemini-3.5-flash-lite"

    def __init__(self, api_key: str, default_repo: str = "gem-bridge"):
        self.api_key = api_key
        self.default_repo = default_repo
        self.client = genai.Client(api_key=api_key) if api_key else None

    def analyze(
        self,
        raw_text: str,
        title: str = "",
        available_repos: Optional[List[str]] = None
    ) -> IntentAnalysisResult:
        available_repos = available_repos or [self.default_repo]
        raw_text_clean = raw_text.lstrip("\ufeff")
        combined_text = f"제목: {title}\n본문:\n{raw_text_clean}".strip()

        # 1. Check for direct JSON payload (e.g. legacy structured requests)
        json_payload = self._extract_raw_json(raw_text_clean)
        if json_payload and isinstance(json_payload, dict):
            direct_result = self._parse_from_json_dict(json_payload, available_repos)
            if direct_result:
                return direct_result

        # 2. Check for key-value / CONTENT_START format (e.g. mobile text commands)
        kv_result = self._extract_raw_key_value(raw_text_clean, available_repos)
        if kv_result:
            return kv_result

        # 3. Check for explicit command prefixes (!분석, !작업, !실행)
        command_hint = self._detect_command_prefix(title, raw_text_clean)

        # 4. LLM structured analysis with gemini-3.6-flash
        if not self.client:
            logger.warning("Gemini Client not initialized (missing API key). Falling back to default READ intent.")
            return self._build_fallback_read_intent(combined_text, available_repos, "Missing API Key")

        try:
            return self._analyze_with_llm(combined_text, available_repos, command_hint)
        except Exception as e:
            logger.error(f"Failed to analyze intent with LLM: {e}. Falling back to safe READ intent.")
            return self._build_fallback_read_intent(
                combined_text, available_repos, f"LLM parsing error fallback: {e}"
            )

    def _extract_raw_key_value(
        self, text: str, available_repos: List[str]
    ) -> Optional[IntentAnalysisResult]:
        cleaned = text.strip().lstrip("\ufeff").strip()

        # 1. Check for ---CONTENT_START--- block
        if "---CONTENT_START---" in cleaned:
            parts = cleaned.split("---CONTENT_START---", 1)
            header_part = parts[0]
            content_part = parts[1]
            if "---CONTENT_END---" in content_part:
                content_part = content_part.split("---CONTENT_END---", 1)[0]
            content = content_part.strip()

            headers = {}
            for line in header_part.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()

            target_path = headers.get("target_path") or headers.get("path") or headers.get("file")
            repo = headers.get("repo") or self.default_repo
            if repo not in available_repos and not (repo.startswith("http") or repo.startswith("git@")):
                repo = self.default_repo
            commit_msg = (
                headers.get("commit_message")
                or headers.get("commit")
                or headers.get("message")
                or f"update: {target_path} via gem-bridge v2"
            )

            if target_path and content:
                return IntentAnalysisResult(
                    task_type=TaskType.WRITE,
                    target_repo=repo,
                    summary=f"Write file: {target_path}",
                    target_path=target_path,
                    content=content,
                    commit_message=commit_msg,
                    reasoning="Direct key-value payload with CONTENT_START block."
                )

        # 2. Check for simple key-value YAML-like format with content:
        lines = cleaned.splitlines()
        kv = {}
        content_lines = []
        is_capturing_content = False
        for line in lines:
            if is_capturing_content:
                content_lines.append(line)
            elif ":" in line and not line.strip().startswith("#"):
                k, v = line.split(":", 1)
                k_norm = k.strip().lower()
                if k_norm in ("repo", "target_path", "path", "file", "commit_message", "commit", "message"):
                    kv[k_norm] = v.strip()
                elif k_norm == "content":
                    is_capturing_content = True
                    if v.strip():
                        content_lines.append(v.strip())

        target_path = kv.get("target_path") or kv.get("path") or kv.get("file")
        if target_path and content_lines:
            repo = kv.get("repo") or self.default_repo
            if repo not in available_repos and not (repo.startswith("http") or repo.startswith("git@")):
                repo = self.default_repo
            commit_msg = (
                kv.get("commit_message")
                or kv.get("commit")
                or kv.get("message")
                or f"update: {target_path} via gem-bridge v2"
            )
            content = "\n".join(content_lines).strip()
            return IntentAnalysisResult(
                task_type=TaskType.WRITE,
                target_repo=repo,
                summary=f"Write file: {target_path}",
                target_path=target_path,
                content=content,
                commit_message=commit_msg,
                reasoning="Direct key-value payload."
            )

        return None

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

    def _extract_raw_json(self, text: str) -> Optional[dict]:
        cleaned = text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
        elif cleaned.startswith("```"):
            cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()

        start_idx = cleaned.find("{")
        end_idx = cleaned.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            candidate = cleaned[start_idx : end_idx + 1]
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return None

    def _parse_from_json_dict(
        self, data: dict, available_repos: List[str]
    ) -> Optional[IntentAnalysisResult]:
        # If the JSON contains explicit write parameters (repo, target_path, content)
        target_path = data.get("target_path")
        content = data.get("content")
        repo = data.get("repo") or self.default_repo
        if repo not in available_repos and not (repo.startswith("http") or repo.startswith("git@")):
            repo = self.default_repo

        if target_path and content is not None:
            commit_msg = data.get("commit_message", f"update: {target_path} via gem-bridge v2")
            return IntentAnalysisResult(
                task_type=TaskType.WRITE,
                target_repo=repo,
                summary=f"Write file: {target_path}",
                target_path=target_path,
                content=content,
                commit_message=commit_msg,
                reasoning="Direct JSON payload provided with target_path and content."
            )

        # If json is a query or command
        if "query" in data or "question" in data:
            query = data.get("query") or data.get("question")
            return IntentAnalysisResult(
                task_type=TaskType.READ,
                target_repo=repo,
                summary=f"Read query: {query[:50]}",
                query=query,
                reasoning="Direct JSON payload provided with read query."
            )

        return None

    def _analyze_with_llm(
        self,
        full_text: str,
        available_repos: List[str],
        command_hint: Optional[TaskType] = None
    ) -> IntentAnalysisResult:
        hint_instruction = ""
        if command_hint:
            hint_instruction = f"사용자가 명시적 커맨드 접두어로 {command_hint.value}를 지정했습니다. 특별한 이유가 없다면 이 유형을 준수하세요.\n"

        prompt = f"""당신은 Git 자동화 브리지 에이전트(gem-bridge v2)의 인텐트 분석기입니다.
사용자가 보낸 문서(제목 및 본문)를 분석하여 작업 유형(TaskType)과 대상 저장소(TargetRepo)를 도출하세요.

[핵심 규칙 - 절대 준수]
1. DEFAULT는 무조건 'READ'입니다.
   - 불명확한 지시, 단순 질문, 아키텍처/코드 조사, 버그 원인 분석, 요약 요청은 파일 수정 없이 무조건 'READ'입니다.
   - 'WRITE'는 파일 생성/수정/이동/리팩토링을 요청한 경우입니다.
     - target_path: 대상 파일 경로 (필수, 예: 'docs/index.html')
     - source_path: 이동/이름변경/참조할 원본 파일 경로가 있다면 지정 (예: 'index.html')
     - content: 직접 파일 본문 전체가 주어진 경우 입력.
     - instruction: 파일 본문이 직접 주어지지 않고 자연어 수정/리팩토링/경로조정 지시사항인 경우 상세히 기술 (예: '루트의 index.html을 docs/index.html로 이동하고 상대 경로를 맞게 수정')
     - commit_message: 명확한 Git 커밋 메시지 (예: 'docs: move index.html to docs/ and fix relative links')
   - 만약 WRITE 지시 같더라도 대상 파일(target_path)이 전혀 특정되지 않거나 수정할 지침(content 또는 instruction)이 없으면 무조건 'READ'로 강등(fallback)하세요.
   - 'EXEC'는 테스트 실행, 빌드, 커맨드라인 명령어 실행을 요청한 경우입니다.
2. TargetRepo 도출:
   - 사용 가능한 로컬 저장소: {available_repos}
   - 또는 텍스트에 포함된 퍼블릭 Git URL (예: https://github.com/...)
   - 저장소가 명시되지 않았다면 가장 적합한 로컬 저장소를 선택하고, 불분명하면 기본값('{self.default_repo}')을 사용하세요.
3. 명시적 커맨드:
   {hint_instruction}
   - '!분석' -> READ
   - '!작업' -> WRITE (단, target_path와 content/instruction이 모두 누락된 경우에만 READ로 강등)
   - '!실행' -> EXEC

[입력 문서 원문]
{full_text}
"""
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=IntentAnalysisResult,
            temperature=0.1,
        )

        models_to_try = [self.MODEL_NAME, "gemini-3.5-flash-lite", "gemini-3.5-flash"]
        response = None
        last_err = None
        for model_name in models_to_try:
            for attempt in range(2):
                try:
                    response = self.client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config=config,
                    )
                    if response and response.text:
                        break
                except Exception as e:
                    last_err = e
                    err_msg = str(e)
                    if "503" in err_msg or "429" in err_msg or "UNAVAILABLE" in err_msg:
                        logger.warning(f"Gemini API error with {model_name} ({e}), retrying in 1s...")
                        time.sleep(1)
                    else:
                        break
            if response and response.text:
                break

        if not response or not response.text:
            raise last_err or RuntimeError("Failed to generate content across all models")

        raw_json_str = response.text.strip()
        result = IntentAnalysisResult.model_validate_json(raw_json_str)

        # Enforce safety guardrail: Default is unconditionally READ
        if result.task_type == TaskType.WRITE:
            has_actionable_spec = bool(result.content or result.instruction or result.source_path)
            if not result.target_path or not has_actionable_spec:
                logger.warning(
                    f"Downgrading WRITE task to READ: missing target_path or actionable spec. Path={result.target_path}"
                )
                result.task_type = TaskType.READ
                result.query = (
                    f"User requested changes but target_path or content/instruction was missing. "
                    f"Original summary: {result.summary}. Full instruction: {full_text}"
                )
                result.reasoning = (
                    f"{result.reasoning or ''} [Guardrail: Downgraded to READ because target_path or actionable instruction was incomplete]"
                ).strip()

        # Target repo fallback
        if not result.target_repo or result.target_repo.strip() == "":
            result.target_repo = self.default_repo

        return result

    def _build_fallback_read_intent(
        self, full_text: str, available_repos: List[str], reason: str
    ) -> IntentAnalysisResult:
        selected_repo = None
        for r in available_repos:
            if re.search(rf"\b{re.escape(r)}\b", full_text, re.IGNORECASE):
                selected_repo = r
                break

        if not selected_repo:
            selected_repo = self.default_repo if self.default_repo in available_repos else (available_repos[0] if available_repos else self.default_repo)

        return IntentAnalysisResult(
            task_type=TaskType.READ,
            target_repo=selected_repo,
            summary="Fallback to READ due to processing error",
            query=full_text,
            reasoning=reason
        )
