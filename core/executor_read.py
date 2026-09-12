import os
import logging
from pathlib import Path
from typing import Dict, List, Optional
from googleapiclient.http import MediaInMemoryUpload
from core.intent_analyzer import IntentAnalysisResult

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

    def __init__(self, drive_service, gemini_client, model_name: str = DEFAULT_MODEL):
        self.drive_service = drive_service
        self.gemini_client = gemini_client
        self.model_name = model_name

    def execute(
        self,
        repo_path: Path,
        intent: IntentAnalysisResult,
        original_title: str = "",
        parent_id: Optional[str] = None
    ) -> Dict[str, str]:
        """
        Executes a READ task without any repository modifications or git push.
        Gathers codebase context, generates report via Gemini, and uploads new Google Doc.
        """
        logger.info(f"[READ Executor] Starting read analysis on repo: {repo_path}")

        # 1. Gather repository context
        repo_context = self._gather_repo_context(repo_path, intent.target_files_or_dirs)

        # 2. Generate analysis report with Gemini
        report_content = self._generate_report(repo_path, intent, repo_context)

        # 3. Create Google Doc on Google Drive
        clean_title = self._clean_doc_title(original_title or intent.summary)
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
            "preview": report_content[:200]
        }

    def _gather_repo_context(
        self,
        repo_path: Path,
        target_files_or_dirs: Optional[List[str]] = None,
        max_total_chars: int = 45000
    ) -> str:
        """Gathers directory tree and relevant file contents within safe size limits."""
        context_parts: List[str] = []

        # Directory tree structure
        tree_lines = self._build_directory_tree(repo_path, max_depth=3)
        context_parts.append(f"### [저장소 디렉토리 구조]\n```\n{tree_lines}\n```\n")

        collected_chars = len(context_parts[0])

        # Priority files: specified target files or key documentation/config files
        candidate_files: List[Path] = []
        if target_files_or_dirs:
            for item in target_files_or_dirs:
                p = (repo_path / item).resolve()
                if p.exists() and p.is_file() and p.is_relative_to(repo_path):
                    candidate_files.append(p)
                elif p.exists() and p.is_dir() and p.is_relative_to(repo_path):
                    for sub in p.rglob("*"):
                        if sub.is_file() and not self._is_ignored(sub):
                            candidate_files.append(sub)

        # Default fallback key files if no specific target files requested
        if not candidate_files:
            priority_names = [
                "README.md", "ARCHITECTURE.md", "pyproject.toml", "package.json",
                "requirements.txt", "main.py", "index.ts", "index.js", "setup.py"
            ]
            for name in priority_names:
                p = (repo_path / name).resolve()
                if p.exists() and p.is_file():
                    candidate_files.append(p)

        # Collect file contents with limits
        for file_path in candidate_files:
            if collected_chars >= max_total_chars:
                break
            try:
                rel_path = file_path.relative_to(repo_path)
                content = file_path.read_text(encoding="utf-8", errors="replace")
                truncated = content[:8000]
                part = f"#### 파일: `{rel_path}`\n```\n{truncated}\n```\n\n"
                context_parts.append(part)
                collected_chars += len(part)
            except Exception as e:
                logger.warning(f"Could not read {file_path} for context: {e}")

        return "\n".join(context_parts)

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

    def _generate_report(
        self,
        repo_path: Path,
        intent: IntentAnalysisResult,
        repo_context: str
    ) -> str:
        """Calls gemini-3.6-flash to produce a comprehensive markdown report."""
        user_query = intent.query or intent.summary
        prompt = f"""당신은 전문 수석 소프트웨어 엔지니어 겸 코드베이스 분석 전문가입니다.
사용자의 분석 요청에 맞춰 로컬 저장소 소스코드 컨텍스트를 바탕으로 상세하고 체계적인 분석 보고서를 작성하세요.

[요청 사항]
- 대상 저장소: {repo_path.name} ({repo_path})
- 분석 주제/질문: {user_query}
- 요청 요약: {intent.summary}

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
        try:
            response = self.gemini_client.models.generate_content(
                model=self.model_name,
                contents=prompt
            )
            return response.text.strip()
        except Exception as e:
            logger.error(f"Error calling Gemini for report generation: {e}")
            return f"""# [보고서] {user_query}

## 1. 개요 및 분석 실패 안내
Gemini 모델({self.model_name}) 호출 중 오류가 발생하여 자동 생성 보고서를 완성하지 못했습니다.
- 에러 원인: {e}

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
