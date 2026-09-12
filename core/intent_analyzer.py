import json
import logging
import re
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


class IntentAnalyzer:
    """
    Parses natural language instructions and explicit commands (!분석, !작업, !실행)
    using gemini-3.6-flash with structured JSON schema enforcement.
    Default task type is strictly READ.
    """

    MODEL_NAME = "gemini-3.6-flash"

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
        combined_text = f"제목: {title}\n본문:\n{raw_text}".strip()

        # 1. Check for direct JSON payload (e.g. legacy structured requests)
        json_payload = self._extract_raw_json(raw_text)
        if json_payload and isinstance(json_payload, dict):
            direct_result = self._parse_from_json_dict(json_payload, available_repos)
            if direct_result:
                return direct_result

        # 2. Check for explicit command prefixes (!분석, !작업, !실행)
        command_hint = self._detect_command_prefix(title, raw_text)

        # 3. LLM structured analysis with gemini-3.6-flash
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
   - 'WRITE'는 오직 사용자가 특정 파일을 생성/수정하도록 명확히 지시하고 반영할 내용(content)이 구체적으로 주어진 경우에만 지정합니다.
   - 만약 WRITE 지시 같더라도 target_path 또는 content가 불분명하거나 누락되었다면 무조건 'READ'로 강등(fallback)하세요.
   - 'EXEC'는 테스트 실행, 빌드, 커맨드라인 명령어 실행을 요청한 경우입니다.
2. TargetRepo 도출:
   - 사용 가능한 로컬 저장소: {available_repos}
   - 또는 텍스트에 포함된 퍼블릭 Git URL (예: https://github.com/...)
   - 저장소가 명시되지 않았다면 가장 적합한 로컬 저장소를 선택하고, 불분명하면 기본값('{self.default_repo}')을 사용하세요.
3. 명시적 커맨드:
   {hint_instruction}
   - '!분석' -> READ
   - '!작업' -> WRITE (단, 구체적인 파일 및 내용이 없으면 READ로 처리)
   - '!실행' -> EXEC

[입력 문서 원문]
{full_text}
"""
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=IntentAnalysisResult,
            temperature=0.1,
        )

        response = self.client.models.generate_content(
            model=self.MODEL_NAME,
            contents=prompt,
            config=config,
        )

        raw_json_str = response.text.strip()
        result = IntentAnalysisResult.model_validate_json(raw_json_str)

        # Enforce safety guardrail: Default is unconditionally READ
        if result.task_type == TaskType.WRITE:
            if not result.target_path or not result.content:
                logger.warning(
                    f"Downgrading WRITE task to READ: missing target_path or content. Path={result.target_path}"
                )
                result.task_type = TaskType.READ
                result.query = (
                    f"User requested changes but target_path or content was missing. "
                    f"Original summary: {result.summary}. Full instruction: {full_text}"
                )
                result.reasoning = (
                    f"{result.reasoning or ''} [Guardrail: Downgraded to READ because target_path or content was incomplete]"
                ).strip()

        # Target repo fallback
        if not result.target_repo or result.target_repo.strip() == "":
            result.target_repo = self.default_repo

        return result

    def _build_fallback_read_intent(
        self, full_text: str, available_repos: List[str], reason: str
    ) -> IntentAnalysisResult:
        repo = available_repos[0] if available_repos else self.default_repo
        return IntentAnalysisResult(
            task_type=TaskType.READ,
            target_repo=repo,
            summary="Fallback to READ due to processing error",
            query=full_text,
            reasoning=reason
        )
