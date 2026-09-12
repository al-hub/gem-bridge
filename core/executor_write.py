import difflib
import fnmatch
import logging
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional
from core.intent_analyzer import IntentAnalysisResult

logger = logging.getLogger("gem_bridge.executor_write")


class ProtectedFileError(Exception):
    """Exception raised when an operation attempts to overwrite a protected file."""
    pass


class WriteExecutor:
    """
    WRITE-only Executor.
    Enforces protected file guardrails, generates diffs, writes updates,
    and executes git commit & push.
    """

    DEFAULT_PROTECTED_PATTERNS = [
        "README*",
        "ARCHITECTURE*",
        "docs/ARCHITECTURE*",
        "LICENSE*",
        "credentials.json",
        "token.json",
        "config.json",
        ".env*",
        ".git/*",
        ".gitignore",
    ]

    def __init__(
        self,
        protected_patterns: Optional[List[str]] = None,
        allow_protected_overwrite: bool = False,
        gemini_client: Optional[object] = None,
    ):
        self.protected_patterns = protected_patterns or self.DEFAULT_PROTECTED_PATTERNS
        self.allow_protected_overwrite = allow_protected_overwrite
        self.gemini_client = gemini_client

    def is_protected(self, rel_path: str) -> bool:
        """Checks whether a relative file path matches protected file patterns."""
        normalized = rel_path.replace("\\", "/").strip().lstrip("/")
        basename = os.path.basename(normalized)

        for pattern in self.protected_patterns:
            # Match against full relative path or basename
            if fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(basename, pattern):
                return True
            # Case-insensitive check
            if fnmatch.fnmatch(normalized.lower(), pattern.lower()) or fnmatch.fnmatch(basename.lower(), pattern.lower()):
                return True
        return False

    def synthesize_code(
        self,
        original_text: str,
        instruction: str,
        target_path: str,
        source_path: Optional[str] = None
    ) -> str:
        """
        Uses Gemini (gemini-3.6-flash) to modify, refactor, or synthesize code
        based on natural language instructions.
        """
        if not self.gemini_client:
            logger.warning("gemini_client not available for code synthesis. Using original text.")
            return original_text

        prompt = f"""당신은 정밀한 소프트웨어 엔지니어입니다.
사용자의 지시사항에 따라 소스 파일의 코드를 수정하거나 리팩토링하세요.

[규칙 - 절대 준수]
1. 반드시 최종 파일의 전체 내용(코드/마크다운/문서 등)만 출력하세요.
2. 앞뒤에 '```' 코드 블록 마크다운을 붙이지 말고 순수 파일 본문 텍스트만 출력하세요.
3. 어떠한 부연 설명, 인사말, 작업 설명도 포함하지 마세요.

[파일 정보]
- 원본 파일 경로: {source_path or target_path}
- 대상 파일 경로: {target_path}

[수정 지시사항]
{instruction}

[기존 파일 내용]
{original_text}
"""
        response = self.gemini_client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
        )
        text = response.text or ""
        # Strip markdown code blocks if the model accidentally included them
        text_stripped = text.strip()
        if text_stripped.startswith("```"):
            lines = text_stripped.splitlines()
            if len(lines) >= 2 and lines[-1].strip() == "```":
                text = "\n".join(lines[1:-1])
            elif text_stripped.endswith("```"):
                text = text_stripped.split("```", 1)[1].rsplit("```", 1)[0]
            if not text.endswith("\n") and original_text.endswith("\n"):
                text += "\n"
        return text

    def execute(
        self,
        repo_path: Path,
        intent: IntentAnalysisResult,
        allow_protected_overwrite: Optional[bool] = None
    ) -> Dict[str, str]:
        """
        Executes a WRITE task.
        1. Validates source and target paths and checks protected file guardrails.
        2. Synthesizes new content via Gemini if natural language instruction is provided.
        3. Moves/renames source file if requested.
        4. Generates unified diff.
        5. Writes changes to disk.
        6. Performs git add, commit, and push.
        """
        if not intent.target_path:
            raise ValueError("WRITE task requires a valid target_path.")

        target_path_str = intent.target_path.replace("\\", "/").strip().lstrip("/")
        full_path = (repo_path / target_path_str).resolve()

        # Security check: Ensure target_path stays strictly inside repo_path
        if not full_path.is_relative_to(repo_path):
            raise PermissionError(
                f"Directory traversal detected! Target path '{intent.target_path}' is outside repo '{repo_path}'."
            )

        source_path_str = None
        source_full_path = None
        if intent.source_path:
            source_path_str = intent.source_path.replace("\\", "/").strip().lstrip("/")
            source_full_path = (repo_path / source_path_str).resolve()
            if not source_full_path.is_relative_to(repo_path):
                raise PermissionError(
                    f"Directory traversal detected! Source path '{intent.source_path}' is outside repo '{repo_path}'."
                )

        # Guardrail check: Protected file overwrite prevention
        allow_override = (
            allow_protected_overwrite
            if allow_protected_overwrite is not None
            else self.allow_protected_overwrite
        )

        if full_path.exists() and self.is_protected(target_path_str):
            if not allow_override:
                err_msg = (
                    f"보호 파일 덮어쓰기 방지 가드레일: '{target_path_str}' 파일은 시스템 보호 대상입니다. "
                    f"덮어쓰기(Overwrite)가 안전하게 차단되었습니다."
                )
                logger.error(err_msg)
                raise ProtectedFileError(err_msg)

        if source_full_path and source_full_path.exists() and self.is_protected(source_path_str):
            if not allow_override:
                err_msg = (
                    f"보호 파일 수정 방지 가드레일: 원본 파일 '{source_path_str}'은 시스템 보호 대상입니다. "
                    f"이동 및 수정이 안전하게 차단되었습니다."
                )
                logger.error(err_msg)
                raise ProtectedFileError(err_msg)

        # Read original content if file already exists
        original_text = ""
        is_new_file = not full_path.exists()
        if not is_new_file:
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    original_text = f.read()
            except Exception as e:
                logger.warning(f"Could not read existing file for diff: {e}")
        elif source_full_path and source_full_path.exists():
            try:
                with open(source_full_path, "r", encoding="utf-8", errors="replace") as f:
                    original_text = f.read()
            except Exception as e:
                logger.warning(f"Could not read existing source file for diff: {e}")

        # Determine new content: explicit content vs LLM code synthesis
        if intent.content is not None and intent.content != "":
            new_content = intent.content
        elif intent.instruction:
            logger.info(f"Synthesizing code for {target_path_str} using instruction: {intent.instruction[:100]}...")
            new_content = self.synthesize_code(
                original_text=original_text,
                instruction=intent.instruction,
                target_path=target_path_str,
                source_path=source_path_str
            )
        else:
            new_content = original_text

        # Generate unified diff
        from_label = f"a/{source_path_str or target_path_str}"
        to_label = f"b/{target_path_str}"
        diff_lines = list(
            difflib.unified_diff(
                original_text.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=from_label,
                tofile=to_label
            )
        )
        diff_output = "".join(diff_lines)
        if diff_output:
            logger.info(f"Generated diff for {target_path_str}:\n{diff_output}")
        else:
            logger.info(f"No diff detected for {target_path_str} (content unchanged or empty).")

        # Write to target file
        full_path.parent.mkdir(parents=True, exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        logger.info(f"Successfully wrote file: {full_path}")

        # Handle file move/removal if source_path differs from target_path
        is_moved = False
        if source_full_path and source_full_path.exists() and source_full_path != full_path:
            try:
                source_full_path.unlink()
                is_moved = True
                logger.info(f"Removed original source file after move: {source_full_path}")
            except Exception as e:
                logger.warning(f"Could not remove original source file {source_full_path}: {e}")

        # Git commit & push
        commit_msg = intent.commit_message or (
            f"refactor: move {source_path_str} to {target_path_str}"
            if is_moved
            else f"update: {target_path_str} via gem-bridge v2"
        )
        commit_hash = self._git_commit_and_push(repo_path, target_path_str, commit_msg)

        return {
            "status": "success",
            "task_type": "WRITE",
            "source_path": source_path_str,
            "target_path": target_path_str,
            "is_moved": is_moved,
            "full_path": str(full_path),
            "is_new_file": is_new_file,
            "diff": diff_output,
            "commit_message": commit_msg,
            "commit_hash": commit_hash
        }

    def _git_commit_and_push(
        self, repo_path: Path, target_path: str, commit_message: str
    ) -> str:
        """Stages file(s), creates git commit, and pushes to remote."""
        # 1. git add target file
        subprocess.run(
            ["git", "add", target_path],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )

        # 2. Also stage deletions or modifications (e.g. moved source file)
        subprocess.run(
            ["git", "add", "-u"],
            cwd=repo_path,
            capture_output=True,
            text=True
        )

        # 3. Check if there are staged changes
        status_res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )
        if not status_res.stdout.strip():
            logger.info(f"No staged changes to commit.")
            # Return current HEAD
            rev_res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True
            )
            return rev_res.stdout.strip()

        # 4. git commit
        subprocess.run(
            ["git", "commit", "-m", commit_message],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )

        # 5. Get commit hash
        rev_res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )
        commit_hash = rev_res.stdout.strip()
        logger.info(f"Committed {commit_hash[:7]}: {commit_message}")

        # 6. git push
        push_res = subprocess.run(
            ["git", "push"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )
        logger.info(f"Successfully pushed to remote: {push_res.stdout.strip() or 'OK'}")

        return commit_hash
