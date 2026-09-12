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
        allow_protected_overwrite: bool = False
    ):
        self.protected_patterns = protected_patterns or self.DEFAULT_PROTECTED_PATTERNS
        self.allow_protected_overwrite = allow_protected_overwrite

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

    def execute(
        self,
        repo_path: Path,
        intent: IntentAnalysisResult,
        allow_protected_overwrite: Optional[bool] = None
    ) -> Dict[str, str]:
        """
        Executes a WRITE task.
        1. Validates target path and checks protected file guardrails.
        2. Generates unified diff.
        3. Writes changes to disk.
        4. Performs git add, commit, and push.
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

        # Read original content if file already exists
        original_text = ""
        is_new_file = not full_path.exists()
        if not is_new_file:
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    original_text = f.read()
            except Exception as e:
                logger.warning(f"Could not read existing file for diff: {e}")

        new_content = intent.content if intent.content is not None else ""

        # Generate unified diff
        diff_lines = list(
            difflib.unified_diff(
                original_text.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"a/{target_path_str}",
                tofile=f"b/{target_path_str}"
            )
        )
        diff_output = "".join(diff_lines)
        if diff_output:
            logger.info(f"Generated diff for {target_path_str}:\n{diff_output}")
        else:
            logger.info(f"No diff detected for {target_path_str} (content unchanged or empty).")

        # Write to file
        full_path.parent.mkdir(parents=True, exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        logger.info(f"Successfully wrote file: {full_path}")

        # Git commit & push
        commit_msg = intent.commit_message or f"update: {target_path_str} via gem-bridge v2"
        commit_hash = self._git_commit_and_push(repo_path, target_path_str, commit_msg)

        return {
            "status": "success",
            "task_type": "WRITE",
            "target_path": target_path_str,
            "full_path": str(full_path),
            "is_new_file": is_new_file,
            "diff": diff_output,
            "commit_message": commit_msg,
            "commit_hash": commit_hash
        }

    def _git_commit_and_push(
        self, repo_path: Path, target_path: str, commit_message: str
    ) -> str:
        """Stages file, creates git commit, and pushes to remote."""
        # 1. git add
        subprocess.run(
            ["git", "add", target_path],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )

        # 2. Check if there are staged changes
        status_res = subprocess.run(
            ["git", "status", "--porcelain", target_path],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )
        if not status_res.stdout.strip():
            logger.info(f"No staged changes to commit for {target_path}.")
            # Return current HEAD
            rev_res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True
            )
            return rev_res.stdout.strip()

        # 3. git commit
        subprocess.run(
            ["git", "commit", "-m", commit_message],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )

        # 4. Get commit hash
        rev_res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )
        commit_hash = rev_res.stdout.strip()
        logger.info(f"Committed {commit_hash[:7]}: {commit_message}")

        # 5. git push
        push_res = subprocess.run(
            ["git", "push"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )
        logger.info(f"Successfully pushed to remote: {push_res.stdout.strip() or 'OK'}")

        return commit_hash
