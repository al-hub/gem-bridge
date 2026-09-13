import os
import time
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from googleapiclient.http import MediaInMemoryUpload
from core.intent_analyzer import IntentAnalysisResult
from core.path_resolver import RepoPathEngine

try:
    from google.genai import types
except ImportError:
    types = None

logger = logging.getLogger("gem_bridge.executor_read")


class ReadExecutor:
    """
    READ-only Executor.
    Strictly forbids git push or any repository modifications.
    Collects codebase context, generates comprehensive analysis via gemini-3.6-flash,
    and returns results by creating a new Google Doc ('[보고서] ...') via Google Drive API.
    """

    DEFAULT_MODEL = "gemini-3.5-flash-lite"
    IGNORED_DIRS = {
        ".git", "__pycache__", "node_modules", ".venv", "venv", ".idea", ".vscode", ".agents"
    }
    IGNORED_EXTENSIONS = {
        ".pyc", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".tar", ".gz"
    }

    def __init__(
        self,
        drive_service,
        gemini_client,
        model_name: str = DEFAULT_MODEL,
        user_gemini_client: Optional[object] = None,
        codeassist_client: Optional[object] = None,
    ):
        self.drive_service = drive_service
        self.gemini_client = gemini_client
        self.model_name = model_name
        self.user_gemini_client = user_gemini_client
        self.codeassist_client = codeassist_client

    def execute(
        self,
        repo_path: Path,
        intent: IntentAnalysisResult,
        original_title: str = "",
        parent_id: Optional[str] = None,
        session_context: Optional[str] = None
    ) -> Dict[str, str]:
        """
        Executes a READ task without any repository modifications or git push.
        Gathers codebase context, generates report via Gemini, and uploads new Google Doc.
        """
        repo_path = Path(repo_path)
        logger.info(f"[READ Executor] Starting read analysis on repo: {repo_path}")

        # 1. Gather repository context & candidate files
        repo_context, candidate_files = self._gather_repo_context(
            repo_path, intent.target_files_or_dirs, return_files=True
        )
        read_mode = self._determine_read_mode(intent, candidate_files)

        # 2. Generate analysis report with Gemini
        report_content = self._generate_report(
            repo_path, intent, repo_context, session_context=session_context, read_mode=read_mode
        )

        # 3. Create Google Doc on Google Drive
        clean_title = self._clean_doc_title(original_title or intent.summary)
        if clean_title.startswith("[📄분석:"):
            doc_name = clean_title
        else:
            doc_name = f"[보고서] {clean_title}"

        created_doc = self._upload_to_drive(doc_name, report_content, parent_id=parent_id)
        doc_id = created_doc.get("id", "")
        logger.info(f"[READ Executor] Successfully created report doc: {doc_name} (ID: {doc_id})")

        return {
            "status": "success",
            "task_type": "READ",
            "doc_id": doc_id,
            "doc_name": doc_name,
            "repo_path": str(repo_path),
            "preview": report_content[:500],
            "report": report_content
        }

    def _determine_read_mode(self, intent: IntentAnalysisResult, candidate_files: Optional[List[Path]] = None) -> str:
        """
        Determines the reporting mode:
        - DOCUMENT_BRIEFING: Single documentation/memoir/text file or query explicitly asking for doc/memo contents.
        - CODE_EXPLAIN: Single code file explanation.
        - CODEBASE_ANALYSIS: Multi-file or general repository architecture analysis.
        """
        candidate_files = candidate_files or []
        user_query = (intent.query or intent.summary or "").lower()
        doc_keywords = ["내용", "요약", "정리", "메모", "기록", "문서", "읽어", "알려줘", "스토리", "history", "story", "brief"]
        is_doc_query = any(k in user_query for k in doc_keywords)

        if len(candidate_files) == 1:
            ext = candidate_files[0].suffix.lower()
            if ext in {".md", ".txt", ".rst", ".doc", ".docx", ".markdown"}:
                return "DOCUMENT_BRIEFING"
            elif ext in {".py", ".ts", ".js", ".go", ".rs", ".java", ".c", ".cpp", ".sh", ".json", ".yaml", ".yml", ".toml", ".sql"}:
                if is_doc_query and ext in {".json", ".yaml", ".yml", ".toml"}:
                    return "DOCUMENT_BRIEFING"
                return "CODE_EXPLAIN"

        if not candidate_files and intent.target_files_or_dirs:
            if all(Path(t).suffix.lower() in {".md", ".txt", ".rst", ".markdown"} for t in intent.target_files_or_dirs):
                return "DOCUMENT_BRIEFING"

        if is_doc_query and candidate_files and all(f.suffix.lower() in {".md", ".txt", ".rst", ".markdown"} for f in candidate_files):
            return "DOCUMENT_BRIEFING"

        return "CODEBASE_ANALYSIS"

    def _gather_repo_context(
        self,
        repo_path: Path,
        target_files_or_dirs: Optional[List[str]] = None,
        max_total_chars: int = 50000,
        return_files: bool = False
    ) -> Any:
        """Gathers directory tree and relevant file contents within safe size limits."""
        context_parts: List[str] = []
        candidate_files: List[Path] = []
        missing_targets: List[str] = []

        # 1. Resolve specific targets if provided using RepoPathEngine
        if target_files_or_dirs:
            for item in target_files_or_dirs:
                resolved = RepoPathEngine.resolve(repo_path, item)
                if resolved:
                    if resolved.is_file() and not self._is_ignored(resolved):
                        candidate_files.append(resolved)
                    elif resolved.is_dir():
                        for sub in sorted(resolved.rglob("*")):
                            if sub.is_file() and not self._is_ignored(sub):
                                candidate_files.append(sub)
                else:
                    missing_targets.append(item)

        is_hero_mode = (len(candidate_files) == 1 and not missing_targets)

        if missing_targets:
            context_parts.append(
                f"### [경고: 요청 파일 미발견]\n"
                f"요청하신 대상 '{', '.join(missing_targets)}'을(를) 저장소 '{repo_path.name}'에서 찾을 수 없습니다.\n\n"
            )

        # 2. Directory tree structure (suppressed in Hero Mode to give full focus to the target document/code)
        if not is_hero_mode:
            tree_lines = self._build_directory_tree(repo_path, max_depth=3)
            context_parts.append(f"### [저장소 디렉토리 구조]\n```\n{tree_lines}\n```\n")
        else:
            context_parts.append(
                f"### [단일 대상 파일 집중 모드 (Hero File Mode)]\n"
                f"대상 파일: `{candidate_files[0].relative_to(repo_path)}`\n\n"
            )

        collected_chars = sum(len(p) for p in context_parts)

        # 3. Default fallback priority files ONLY if no target files were requested
        if not target_files_or_dirs and not candidate_files:
            priority_names = [
                "README.md", "ARCHITECTURE.md", "pyproject.toml", "package.json",
                "requirements.txt", "main.py", "index.ts", "index.js", "setup.py"
            ]
            for name in priority_names:
                resolved = RepoPathEngine.resolve(repo_path, name)
                if resolved and resolved.is_file():
                    candidate_files.append(resolved)

        # 4. Collect file contents
        single_file_limit = 50000 if is_hero_mode else 12000
        for file_path in candidate_files:
            if collected_chars >= max_total_chars:
                break
            try:
                rel_path = file_path.relative_to(repo_path)
                content = file_path.read_text(encoding="utf-8", errors="replace")
                truncated = content[:single_file_limit]
                if len(content) > single_file_limit:
                    truncated += f"\n\n... (총 {len(content)}자 중 {single_file_limit}자 표시됨) ..."
                part = f"#### 파일: `{rel_path}`\n```\n{truncated}\n```\n\n"
                context_parts.append(part)
                collected_chars += len(part)
            except Exception as e:
                logger.warning(f"Could not read {file_path} for context: {e}")

        full_context = "\n".join(context_parts)
        if return_files:
            return full_context, candidate_files
        return full_context

    def _build_directory_tree(self, root_dir: Path, max_depth: int = 3) -> str:
        lines: List[str] = []

        def _traverse(curr_dir: Path, prefix: str, depth: int):
            if depth > max_depth:
                return
            try:
                entries = sorted(curr_dir.iterdir(), key=lambda x: (not x.is_dir(), x.name))
            except PermissionError:
                return

            visible_entries = [e for e in entries if not self._is_ignored(e)]
            for i, entry in enumerate(visible_entries):
                is_last = (i == len(visible_entries) - 1)
                connector = "└── " if is_last else "├── "
                sub_prefix = "    " if is_last else "│   "

                lines.append(f"{prefix}{connector}{entry.name}{'/' if entry.is_dir() else ''}")

                if entry.is_dir():
                    _traverse(entry, prefix + sub_prefix, depth + 1)

        _traverse(root_dir, "", 1)
        return "\n".join(lines[:100])

    def _is_ignored(self, path: Path) -> bool:
        if path.name.startswith(".") and path.name != ".env":
            return True
        if path.name in self.IGNORED_DIRS:
            return True
        for part in path.parts:
            if part in self.IGNORED_DIRS:
                return True
        if path.suffix.lower() in self.IGNORED_EXTENSIONS:
            return True
        return False

    @staticmethod
    def _build_model_config(model_name: str) -> Optional[Any]:
        """
        Builds optimized GenerateContentConfig for thinking models (3.8-flash, 3.7-flash).
        Setting thinking_budget=0 bypasses the congested dynamic reasoning queue (503 UNAVAILABLE)
        and routes directly to standard high-throughput Flash TPU clusters.
        """
        if ("3.8" in model_name or "3.7" in model_name) and types is not None:
            try:
                return types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_budget=0)
                )
            except Exception:
                return None
        return None

    def _generate_report(
        self,
        repo_path: Path,
        intent: IntentAnalysisResult,
        repo_context: str,
        session_context: Optional[str] = None,
        read_mode: str = "CODEBASE_ANALYSIS"
    ) -> str:
        """Calls Gemini tiered models to produce a comprehensive markdown report."""
        user_query = intent.query or intent.summary
        session_block = f"\n[이전 연속 작업 세션 맥락]\n{session_context.strip()}\n" if session_context and session_context.strip() else ""

        if read_mode == "DOCUMENT_BRIEFING":
            prompt = f"""당신은 전문 리서치 분석가 및 도큐먼트 전문가입니다.
사용자의 문서 열람 및 요약 요청에 맞춰 수집된 원문 컨텍스트를 바탕으로 핵심 내용이 즉시 전달되는 명확한 브리핑 보고서를 작성하세요.

[요청 사항]
- 대상 저장소: {repo_path.name} ({repo_path})
- 분석 주제/질문: {user_query}
- 요청 요약: {intent.summary}
{session_block}
[수집된 문서 원문 컨텍스트]
{repo_context}

[보고서 작성 가이드라인]
1. 깔끔하고 가독성 높은 마크다운 형식으로 작성하세요.
2. 불필요한 코드베이스 아키텍처나 코드 린터 목차를 강제하지 말고, 아래와 같이 문서 내용 중심의 목차로 작성하세요:
   # [보고서] {user_query}
   ## 1. 핵심 요약 (Executive Summary) - 3~5줄 내외의 명확한 핵심 줄거리 및 결론
   ## 2. 주요 내용 및 상세 해설 (Key Points & Breakdown) - 원문의 핵심 사실, 맥락, 기록 사항 정리
   ## 3. 핵심 시사점 및 맥락 (Insights & Context)
3. 기술적 수사구나 허위 내용을 지어내지 말고, 오직 제공된 문서 원문에 기반하여 진실되고 충실하게 작성하세요.
"""
        elif read_mode == "CODE_EXPLAIN":
            prompt = f"""당신은 전문 시니어 소프트웨어 엔지니어입니다.
사용자의 특정 소스코드 분석 요청에 맞춰 해당 모듈의 책임과 구현 상세를 명확히 설명하는 분석 보고서를 작성하세요.

[요청 사항]
- 대상 저장소: {repo_path.name} ({repo_path})
- 분석 주제/질문: {user_query}
- 요청 요약: {intent.summary}
{session_block}
[수집된 소스코드 컨텍스트]
{repo_context}

[보고서 작성 가이드라인]
1. 깔끔하고 전문적인 마크다운 형식으로 작성하세요.
2. 아래 목차 구성을 포함하여 작성하세요:
   # [보고서] {user_query}
   ## 1. 모듈 개요 및 주요 책임 (Purpose & Responsibility)
   ## 2. 주요 클래스 및 함수 상세 분석 (Core Implementation)
   ## 3. 실행 로직 흐름 및 사용 예시 (Flow & Usage)
   ## 4. 품질, 예외 처리 및 개선 포인트 (Quality & Recommendations)
3. 코드 발췌문(Snippet)이 필요할 경우 정확한 코드 블록을 제공하세요.
4. 제공된 소스코드 컨텍스트에 기반하여 정확하게 설명하세요.
"""
        else:
            prompt = f"""당신은 전문 수석 소프트웨어 엔지니어 겸 코드베이스 분석 전문가입니다.
사용자의 분석 요청에 맞춰 로컬 저장소 소스코드 컨텍스트를 바탕으로 상세하고 체계적인 분석 보고서를 작성하세요.

[요청 사항]
- 대상 저장소: {repo_path.name} ({repo_path})
- 분석 주제/질문: {user_query}
- 요청 요약: {intent.summary}
{session_block}
[수집된 소스코드 및 저장소 컨텍스트]
{repo_context}

[보고서 작성 가이드라인]
1. 깔끔하고 전문적인 마크다운 형식으로 작성하세요.
2. 아래 목차 구성을 반드시 포함하세요:
   # [보고서] {user_query}
   ## 1. 개요 및 목적 (Executive Summary)
   ## 2. 코드베이스 구조 및 주요 컴포넌트 분석
   ## 3. 핵심 발견점 및 상세 답변 (Deep Dive)
   ## 4. 권장사항 및 개선점 (Recommendations & Next Steps)
3. 코드 발췌문(Snippet)이 필요할 경우 파일 경로와 함께 정확한 코드 블록을 제공하세요.
4. 절대 가상의 내용을 지어내지 말고, 제공된 컨텍스트에 기반하여 정확하게 설명하세요.
"""
        is_deep = getattr(intent, "model_tier", "default") == "deep"

        # Tier-0 Priority: Google Code Assist Direct Bridge (agy OAuth session)
        if self.codeassist_client and getattr(self.codeassist_client, "is_available", lambda: False)() and is_deep:
            try:
                logger.info("[READ Executor] Attempting Tier-0 report generation with gemini-3.8-flash-tiered via CodeAssist...")
                c_text = self.codeassist_client.generate_content(
                    prompt=prompt,
                    model="gemini-3.8-flash-tiered"
                )
                if c_text:
                    logger.info("[READ Executor] Successfully generated report using gemini-3.8-flash-tiered (CodeAssist Tier-0).")
                    return c_text.strip()
            except Exception as e:
                logger.warning(f"[READ Executor] Tier-0 CodeAssist report generation failed with gemini-3.8-flash-tiered: {e}. Cascading to Tier-1 User OAuth...")

        # Tier-1 Priority: User OAuth account with gemini-3.8-flash (if available and is_deep)
        if self.user_gemini_client and is_deep:
            try:
                logger.info("[READ Executor] Attempting Tier-1 report generation with gemini-3.8-flash via User OAuth...")
                kwargs = {"model": "gemini-3.8-flash", "contents": prompt}
                cfg = self._build_model_config("gemini-3.8-flash")
                if cfg is not None:
                    kwargs["config"] = cfg
                user_resp = self.user_gemini_client.models.generate_content(**kwargs)
                if user_resp and user_resp.text:
                    logger.info("[READ Executor] Successfully generated report using gemini-3.8-flash (User OAuth).")
                    return user_resp.text.strip()
            except Exception as e:
                logger.warning(f"[READ Executor] Tier-1 User OAuth report generation failed with gemini-3.8-flash: {e}. Cascading to API Key fallback chain...")

        deep_chain = [
            "gemini-3.8-flash",
            "gemini-3.7-flash",
            "gemini-3.6-flash",
            "gemini-3.5-flash",
            "gemini-3.5-flash-lite",
        ]
        default_chain = [self.model_name, "gemini-3.5-flash"]
        models_to_try = deep_chain if is_deep else default_chain

        response = None
        last_error = None
        for model_name in models_to_try:
            try:
                logger.info(f"[READ Executor] Generating report with {model_name} (tier: {getattr(intent, 'model_tier', 'default')})...")
                kwargs = {"model": model_name, "contents": prompt}
                cfg = self._build_model_config(model_name)
                if cfg is not None:
                    kwargs["config"] = cfg
                response = self.gemini_client.models.generate_content(**kwargs)
                if response and response.text:
                    logger.info(f"[READ Executor] Successfully generated report using {model_name}.")
                    return response.text.strip()
            except Exception as e:
                last_error = e
                logger.warning(f"[READ Executor] Report generation with {model_name} failed: {e}")

        logger.error(f"All models failed for report generation. Last error: {last_error}")
        return f"""# [보고서] {user_query}

## 1. 개요 및 분석 실패 안내
Gemini 모델 호출 중 오류가 발생하여 자동 생성 보고서를 완성하지 못했습니다.
- 에러 원인: {last_error}

## 2. 수집된 저장소 컨텍스트 요약
{repo_context[:3000]}
"""

    def _upload_to_drive(self, title: str, content: str, parent_id: Optional[str] = None) -> dict:
        """Uploads plain text as a native Google Doc via Drive API."""
        if not self.drive_service:
            logger.warning("Drive service is None; skipping drive upload.")
            return {"id": "mock_id", "name": title}

        media = MediaInMemoryUpload(
            content.encode("utf-8"),
            mimetype="text/plain",
            resumable=True
        )
        file_metadata = {
            "name": title,
            "mimeType": "application/vnd.google-apps.document"
        }
        if parent_id:
            file_metadata["parents"] = [parent_id]

        return self.drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, name, webViewLink"
        ).execute()

    def _clean_doc_title(self, raw_title: str) -> str:
        """Cleans doc title by removing trigger commands and prefixes."""
        clean = raw_title.strip()
        clean = clean.replace("[보고서]", "").strip()
        for prefix in ["!분석", "!analyze", "!조회", "!read", "!작업", "!task", "!실행", "!exec", "!"]:
            if clean.startswith(prefix):
                clean = clean[len(prefix):].strip()
        return clean or "코드베이스 분석 보고서"
