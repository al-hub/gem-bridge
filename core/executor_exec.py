import logging
import shlex
import subprocess
from pathlib import Path
from typing import Dict, List, Optional
from googleapiclient.http import MediaInMemoryUpload
from core.intent_analyzer import IntentAnalysisResult

logger = logging.getLogger("gem_bridge.executor_exec")


class CommandSafetyError(Exception):
    """Exception raised when an unsafe or blocked command execution is requested."""
    pass


class ExecExecutor:
    """
    EXEC Executor.
    Executes specified shell commands/tests with safety guardrails within the repository.
    Optionally reports execution results to Google Drive.
    """

    BLOCKED_PATTERNS = [
        "rm -rf /",
        "rm -rf ~",
        "mkfs",
        "dd if=",
        ":(){ :|:& };:",
        "chmod -r 777 /",
        "shutdown",
        "reboot",
        "poweroff",
    ]

    def __init__(self, drive_service=None, default_timeout: int = 60):
        self.drive_service = drive_service
        self.default_timeout = default_timeout

    def execute(
        self,
        repo_path: Path,
        intent: IntentAnalysisResult,
        original_title: str = ""
    ) -> Dict[str, str]:
        """Executes a command safely within repo_path."""
        command = intent.exec_command or intent.summary
        if not command or not command.strip():
            raise ValueError("EXEC task requires a valid command.")

        command = command.strip()
        self._verify_command_safety(command)

        logger.info(f"[EXEC Executor] Running command in {repo_path}: {command}")
        try:
            res = subprocess.run(
                command,
                cwd=repo_path,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.default_timeout
            )
            stdout = res.stdout.strip()
            stderr = res.stderr.strip()
            exit_code = res.returncode
            success = (exit_code == 0)
        except subprocess.TimeoutExpired as te:
            stdout = te.stdout or ""
            stderr = f"Command timed out after {self.default_timeout} seconds."
            exit_code = -1
            success = False
        except Exception as e:
            stdout = ""
            stderr = f"Unexpected execution error: {e}"
            exit_code = -1
            success = False

        result_doc_name = f"[실행결과] {self._clean_title(original_title or command)}"
        report_md = f"""# {result_doc_name}

## 1. 실행 개요
- 대상 저장소: `{repo_path}`
- 실행 명령어: `{command}`
- 종료 코드: `{exit_code}` ({'성공' if success else '실패'})

## 2. 표준 출력 (stdout)
```
{stdout if stdout else '(출력 없음)'}
```

## 3. 에러 출력 (stderr)
```
{stderr if stderr else '(에러 없음)'}
```
"""
        doc_id = ""
        if self.drive_service:
            try:
                media = MediaInMemoryUpload(
                    report_md.encode("utf-8"),
                    mimetype="text/plain",
                    resumable=True
                )
                created = self.drive_service.files().create(
                    body={
                        "name": result_doc_name,
                        "mimeType": "application/vnd.google-apps.document"
                    },
                    media_body=media,
                    fields="id, name"
                ).execute()
                doc_id = created.get("id", "")
            except Exception as e:
                logger.error(f"Failed to upload exec report to drive: {e}")

        return {
            "status": "success" if success else "failed",
            "task_type": "EXEC",
            "command": command,
            "exit_code": str(exit_code),
            "stdout": stdout,
            "stderr": stderr,
            "doc_id": doc_id,
            "doc_name": result_doc_name
        }

    def _verify_command_safety(self, command: str):
        cmd_lower = command.lower()
        for blocked in self.BLOCKED_PATTERNS:
            if blocked in cmd_lower:
                raise CommandSafetyError(
                    f"Command '{command}' contains dangerous pattern '{blocked}' and is blocked."
                )

    def _clean_title(self, raw_title: str) -> str:
        clean = raw_title.strip()
        clean = clean.replace("[실행결과]", "").strip()
        for prefix in ["!실행", "!exec", "!run", "!"]:
            if clean.startswith(prefix):
                clean = clean[len(prefix):].strip()
        return clean or "명령어 실행 결과"
