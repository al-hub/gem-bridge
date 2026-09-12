import json
import logging
import os
import signal
import sys
import time
import traceback
from pathlib import Path
from typing import Dict, List, Optional, Set
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload
from google import genai

from core import __version__
from core.intent_analyzer import IntentAnalyzer, IntentAnalysisResult, TaskType
from core.repo_manager import RepoManager, RepoError
from core.executor_read import ReadExecutor
from core.executor_write import WriteExecutor, ProtectedFileError
from core.executor_exec import ExecExecutor
from core.console_protocol import (
    ConsoleProtocolParser,
    ConsoleDocFormatter,
    DEFAULT_PLACEHOLDER,
    OUTPUT_SECTION_HEADER,
)
from core.telemetry import TimeTagFormatter, PipelineProfiler
from core.drive_storage import DriveStorageManager
from core.janitor import StorageJanitor


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
TOKEN_PATH = BASE_DIR / "token.json"
LOG_PATH = BASE_DIR / "result.log"

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_PATH, encoding="utf-8", mode="a")
    ]
)
logger = logging.getLogger("gem_bridge.daemon_v2")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        logger.warning(f"Config file not found at {CONFIG_PATH}. Using empty defaults.")
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_drive_service(token_path: Path = TOKEN_PATH):
    if not token_path.exists():
        raise FileNotFoundError(f"token.json not found at {token_path}. Run auth_helper.py first.")
    with open(token_path, "r", encoding="utf-8") as f:
        creds_data = json.load(f)
    creds = Credentials.from_authorized_user_info(creds_data)
    return build("drive", "v3", credentials=creds)


class GemBridgeDaemonV2:
    """
    gem-bridge v2 Dispatcher Daemon with Bi-Directional Mobile CONSOLE Support.
    Polls Google Drive for incoming task documents and the dedicated CONSOLE doc,
    determines intent, dynamically prepares repositories, and dispatches to executors.
    """

    SYSTEM_DOC_PREFIXES = [
        "[보고서]", "[완료]", "[오류]", "[실행결과]",
        "[📄분석", "[✅커밋", "[💻실행", "[⚠️오류", "[📌"
    ]
    TRIGGER_KEYWORDS = ["!", "깃", "task", "작업", "분석", "실행", "gem-bridge", "보고서", "console", "gemini"]

    def __init__(self, config: Optional[dict] = None):
        self.config = config or load_config()
        self.poll_interval = self.config.get("poll_interval_seconds", 5)
        self.active_poll_interval: float = 1.0
        self.idle_poll_interval: float = float(self.poll_interval)
        self._last_activity_time: float = time.time()
        self.gemini_api_key = self.config.get("gemini_api_key", "")
        self.repo_mapping = self.config.get("repositories", {})

        # Initialize Google Drive service
        try:
            self.drive_service = get_drive_service()
            logger.info("Google Drive service successfully initialized.")
        except Exception as e:
            logger.error(f"Failed to initialize Google Drive service: {e}")
            self.drive_service = None

        # Initialize Gemini Client
        self.gemini_client = genai.Client(api_key=self.gemini_api_key) if self.gemini_api_key else None

        # Initialize core components
        self.repo_manager = RepoManager(repo_mapping=self.repo_mapping)
        self.intent_analyzer = IntentAnalyzer(
            api_key=self.gemini_api_key,
            default_repo="gem-bridge"
        )
        self.read_executor = ReadExecutor(
            drive_service=self.drive_service,
            gemini_client=self.gemini_client
        )
        self.write_executor = WriteExecutor(
            protected_patterns=self.config.get("protected_files"),
            gemini_client=self.gemini_client
        )
        self.exec_executor = ExecExecutor(drive_service=self.drive_service)

        self.processed_ids: Set[str] = set()

        # CONSOLE and STATUS doc tracking
        self.folder_id: Optional[str] = self._find_or_create_folder()
        self.storage_manager = DriveStorageManager(self.drive_service, self.folder_id) if self.drive_service and self.folder_id else None
        self.janitor = StorageJanitor(self.drive_service, self.folder_id, storage_manager=self.storage_manager) if self.drive_service and self.folder_id else None
        self._last_janitor_run_time: float = time.time()
        self.status_doc_id: Optional[str] = self._init_status_doc()
        self.console_doc_id: Optional[str] = None
        self._last_console_content_hash: Optional[str] = None
        self._last_processed_command_hash: Optional[str] = None
        self._last_console_modified_time: Optional[str] = None
        self._last_heartbeat_time: float = time.time()

        if self.drive_service:
            self.console_doc_id = self._init_console_doc()

        # Register graceful shutdown signals (SIGTERM, SIGINT)
        try:
            signal.signal(signal.SIGTERM, self._handle_shutdown)
            signal.signal(signal.SIGINT, self._handle_shutdown)
        except (ValueError, AttributeError):
            pass

    def get_sleep_interval(self) -> float:
        """Returns 1.0s during active periods (within 5 minutes of activity), else idle interval."""
        if time.time() - self._last_activity_time < 300:
            return self.active_poll_interval
        return self.idle_poll_interval

    def _find_or_create_folder(self) -> Optional[str]:
        if not self.drive_service:
            return None
        try:
            folder_res = self.drive_service.files().list(
                q="mimeType = 'application/vnd.google-apps.folder' and name = 'GeminiBridge' and trashed = false",
                fields="files(id, name)"
            ).execute()
            folders = folder_res.get("files", [])
            if folders:
                return folders[0]["id"]
            # Create folder if missing
            body = {
                "name": "GeminiBridge",
                "mimeType": "application/vnd.google-apps.folder"
            }
            created = self.drive_service.files().create(body=body, fields="id").execute()
            logger.info(f"Created GeminiBridge folder: {created.get('id')}")
            return created.get("id")
        except Exception as e:
            logger.warning(f"Could not find or create GeminiBridge folder: {e}")
            return None

    def _init_status_doc(self) -> Optional[str]:
        if not self.drive_service:
            return None
        try:
            q = "name = 'STATUS' and trashed = false"
            if self.folder_id:
                q += f" and '{self.folder_id}' in parents"
            res = self.drive_service.files().list(q=q, fields="files(id, name)").execute()
            files = res.get("files", [])
            if files:
                return files[0]["id"]

            initial_text = f"# 🟢 gem-bridge 시스템 가동 중\n- 상태: 대기 중\n- 시각: {time.strftime('%Y-%m-%d %H:%M:%S')}"
            media = MediaInMemoryUpload(initial_text.encode("utf-8"), mimetype="text/plain")
            body = {"name": "STATUS", "mimeType": "application/vnd.google-apps.document"}
            if self.folder_id:
                body["parents"] = [self.folder_id]
            created = self.drive_service.files().create(body=body, media_body=media, fields="id").execute()
            return created.get("id")
        except Exception as e:
            logger.warning(f"Could not initialize STATUS document: {e}")
            return None

    def _init_console_doc(self) -> Optional[str]:
        """Finds or creates the GeminiBridge/[최신결과] CONSOLE doc and marks it ONLINE."""
        if not self.drive_service:
            return None
        try:
            q = "(name = '[최신결과] CONSOLE' or name = 'CONSOLE') and trashed = false"
            if self.folder_id:
                q += f" and '{self.folder_id}' in parents"
            res = self.drive_service.files().list(q=q, fields="files(id, name, modifiedTime)").execute()
            files = res.get("files", [])

            if files:
                doc_id = files[0]["id"]
                file_name = files[0].get("name", "")
                self.console_doc_id = doc_id

                # If legacy name 'CONSOLE', rename to '[최신결과] CONSOLE' for optimal Gemini Search matching
                if file_name == "CONSOLE":
                    try:
                        self.drive_service.files().update(
                            fileId=doc_id,
                            body={"name": "[최신결과] CONSOLE"}
                        ).execute()
                        logger.info("Renamed legacy CONSOLE doc to '[최신결과] CONSOLE'.")
                    except Exception as rename_err:
                        logger.warning(f"Could not rename CONSOLE doc: {rename_err}")

                # Read existing to preserve history/command, then update badge to ONLINE
                try:
                    raw_content = self.drive_service.files().export_media(
                        fileId=doc_id, mimeType="text/plain"
                    ).execute().decode("utf-8")
                    cmd = ConsoleProtocolParser.extract_command(raw_content) or DEFAULT_PLACEHOLDER
                    history = ConsoleProtocolParser.extract_history(raw_content)

                    # Startup crash self-healing: if stuck in PROCESSING, recover to ONLINE
                    recovered_msg = "*(PC 데몬이 정상 가동되었습니다. 아래 입력창에 작업을 입력하세요.)*"
                    if "PROCESSING" in raw_content:
                        logger.info("Auto-healing: Resetting previous zombie PROCESSING state to ONLINE.")
                        recovered_msg = "*(시스템 재부팅: 이전 비정상 종료된 작업이 정리되고 ONLINE으로 자동 복구되었습니다.)*"

                    online_text = ConsoleDocFormatter.render(
                        status="ONLINE",
                        input_command=cmd,
                        output_content=recovered_msg,
                        history_items=history
                    )
                    self._write_console_content(online_text)
                    self._last_console_content_hash = ConsoleProtocolParser.compute_content_hash(online_text)
                    logger.info(f"Loaded existing GeminiBridge/[최신결과] CONSOLE doc: {doc_id} (Marked ONLINE)")
                except Exception as read_err:
                    logger.warning(f"Could not read/update existing CONSOLE doc: {read_err}")
                return doc_id

            # Create new [최신결과] CONSOLE doc
            initial_text = ConsoleDocFormatter.render(
                status="ONLINE",
                input_command=DEFAULT_PLACEHOLDER,
                output_content="*(gem-bridge v2 시스템이 시작되었습니다. 아래 입력창에 작업을 입력하세요.)*"
            )
            media = MediaInMemoryUpload(initial_text.encode("utf-8"), mimetype="text/plain")
            body = {"name": "[최신결과] CONSOLE", "mimeType": "application/vnd.google-apps.document"}
            if self.folder_id:
                body["parents"] = [self.folder_id]
            created = self.drive_service.files().create(
                body=body,
                media_body=media,
                fields="id, modifiedTime"
            ).execute()
            doc_id = created.get("id")
            self.console_doc_id = doc_id
            self._last_console_modified_time = created.get("modifiedTime")
            self._last_console_content_hash = ConsoleProtocolParser.compute_content_hash(initial_text)
            logger.info(f"Created new GeminiBridge/[최신결과] CONSOLE doc: {doc_id}")
            return doc_id
        except Exception as e:
            logger.warning(f"Could not initialize CONSOLE document: {e}")
            return None

    def _read_console_content(self) -> Optional[str]:
        """Safely reads the current CONSOLE document plain text content."""
        if not self.drive_service or not self.console_doc_id:
            return None
        try:
            return self.drive_service.files().export_media(
                fileId=self.console_doc_id,
                mimeType="text/plain"
            ).execute().decode("utf-8")
        except Exception as e:
            logger.warning(f"Failed to read CONSOLE doc content: {e}")
            return None

    def _write_console_content(self, text: str):
        """Safely updates CONSOLE doc content and tracks modifiedTime."""
        if not self.drive_service or not self.console_doc_id:
            return
        try:
            media = MediaInMemoryUpload(text.encode("utf-8"), mimetype="text/plain")
            updated = self.drive_service.files().update(
                fileId=self.console_doc_id,
                media_body=media,
                fields="id, modifiedTime"
            ).execute()
            self._last_console_modified_time = updated.get("modifiedTime")
            self._last_console_content_hash = ConsoleProtocolParser.compute_content_hash(text)
        except Exception as e:
            logger.warning(f"Failed to write CONSOLE doc content: {e}")

    def _handle_shutdown(self, signum=None, frame=None):
        """Graceful shutdown hook: Marks CONSOLE and STATUS as OFFLINE before exiting."""
        logger.info(f"Shutdown signal ({signum}) received. Setting CONSOLE and STATUS to OFFLINE...")
        self._set_console_offline()
        self._update_status(f"# 🔴 gem-bridge 시스템 종료됨\n- 시각: {time.strftime('%Y-%m-%d %H:%M:%S')} KST\n- PC 전원이 안전하게 종료되었습니다.")
        sys.exit(0)

    def _set_console_offline(self):
        """Updates CONSOLE status badge to OFFLINE while preserving input and history."""
        if not self.drive_service or not self.console_doc_id:
            return
        try:
            raw_content = self.drive_service.files().export_media(
                fileId=self.console_doc_id,
                mimeType="text/plain"
            ).execute().decode("utf-8")
            cmd = ConsoleProtocolParser.extract_command(raw_content) or DEFAULT_PLACEHOLDER
            history = ConsoleProtocolParser.extract_history(raw_content)

            output = "*(PC 전원이 종료되었습니다. 작업을 입력해 두시면 재부팅 시 자동 실행됩니다.)*"
            if OUTPUT_SECTION_HEADER in raw_content:
                parts = raw_content.split(OUTPUT_SECTION_HEADER, 1)[1]
                if "════" in parts:
                    output = parts.split("════", 1)[0].strip()

            offline_text = ConsoleDocFormatter.render(
                status="OFFLINE",
                input_command=cmd,
                output_content=output,
                history_items=history
            )
            self._write_console_content(offline_text)
            logger.info("Successfully marked CONSOLE doc as OFFLINE.")
        except Exception as e:
            logger.warning(f"Could not set CONSOLE doc to OFFLINE: {e}")

    def _update_console_heartbeat(self):
        """Updates timestamp on CONSOLE doc if idle for more than 5 minutes."""
        if not self.drive_service or not self.console_doc_id:
            return
        try:
            raw_content = self.drive_service.files().export_media(
                fileId=self.console_doc_id,
                mimeType="text/plain"
            ).execute().decode("utf-8")
            cmd = ConsoleProtocolParser.extract_command(raw_content) or DEFAULT_PLACEHOLDER
            history = ConsoleProtocolParser.extract_history(raw_content)

            output = "*(대기 중)*"
            if OUTPUT_SECTION_HEADER in raw_content:
                parts = raw_content.split(OUTPUT_SECTION_HEADER, 1)[1]
                if "════" in parts:
                    output = parts.split("════", 1)[0].strip()

            refreshed_text = ConsoleDocFormatter.render(
                status="ONLINE",
                input_command=cmd,
                output_content=output,
                history_items=history
            )
            self._write_console_content(refreshed_text)
            logger.info("Heartbeat: refreshed CONSOLE doc online timestamp.")
        except Exception as e:
            logger.warning(f"Failed to refresh CONSOLE heartbeat: {e}")

    def _update_status(self, text: str):
        if not self.drive_service or not self.status_doc_id:
            return
        try:
            media = MediaInMemoryUpload(text.encode("utf-8"), mimetype="text/plain")
            self.drive_service.files().update(
                fileId=self.status_doc_id,
                media_body=media
            ).execute()
            logger.info("Updated GeminiBridge/STATUS document.")
        except Exception as e:
            logger.warning(f"Failed to update STATUS document: {e}")

    def check_and_process_console(self):
        """
        Polls the dedicated CONSOLE document, verifies changes against 3-layer
        anti-echo guards, dispatches the command, and writes results back.
        """
        if not self.drive_service or not self.console_doc_id:
            return

        try:
            # 1. Fetch metadata to check modifiedTime
            meta = self.drive_service.files().get(
                fileId=self.console_doc_id,
                fields="id, name, modifiedTime"
            ).execute()
            modified_time = meta.get("modifiedTime")
            if modified_time and modified_time == self._last_console_modified_time:
                return

            # 2. Export document text
            raw_content = self.drive_service.files().export_media(
                fileId=self.console_doc_id,
                mimeType="text/plain"
            ).execute().decode("utf-8")

            # 3. Layer 1 Anti-Echo: Full content hash
            content_hash = ConsoleProtocolParser.compute_content_hash(raw_content)
            if self._last_console_content_hash and content_hash == self._last_console_content_hash:
                self._last_console_modified_time = modified_time
                return

            # 4. Extract command
            cmd = ConsoleProtocolParser.extract_command(raw_content)
            if not cmd:
                self._last_console_content_hash = content_hash
                self._last_console_modified_time = modified_time
                return

            # 5. Layer 2 Anti-Echo: Command hash
            cmd_hash = ConsoleProtocolParser.compute_command_hash(cmd)
            if self._last_processed_command_hash and cmd_hash == self._last_processed_command_hash:
                self._last_console_content_hash = content_hash
                self._last_console_modified_time = modified_time
                return

            self._last_activity_time = time.time()
            trace_id = TimeTagFormatter.generate_trace_id()
            sync_lag_ms = TimeTagFormatter.calculate_sync_lag_ms(modified_time)
            profiler = PipelineProfiler(trace_id=trace_id)

            logger.info(f"=== [{trace_id}][CONSOLE] Command detected: '{cmd}' | sync_lag={int(sync_lag_ms)}ms ===")
            now_str = TimeTagFormatter.format_kst()

            # Extract existing history before processing
            history = ConsoleProtocolParser.extract_history(raw_content)

            # 6. Set PROCESSING status badge
            with profiler.step("set_processing"):
                proc_text = ConsoleDocFormatter.render(
                    status="PROCESSING",
                    input_command=cmd,
                    output_content="*(현재 작업을 수행하고 있습니다. 잠시만 기다려 주세요...)*",
                    history_items=history,
                    trace_id=trace_id,
                    sync_lag_ms=sync_lag_ms
                )
                self._write_console_content(proc_text)

            # 7. Analyze Intent (Fast-Path Regex or Gemini LLM)
            intent = None
            if self._is_janitor_command(cmd):
                intent = IntentAnalysisResult(
                    task_type=TaskType.EXEC,
                    target_repo="gem-bridge",
                    summary=cmd,
                    exec_command=cmd,
                    reasoning="Fast-path: Maintenance/Janitor command"
                )
            elif cmd.startswith("!실행 ") or cmd.startswith("!exec "):
                exec_cmd = cmd.split(None, 1)[1].strip()
                intent = IntentAnalysisResult(
                    task_type=TaskType.EXEC,
                    target_repo="gem-bridge",
                    summary=exec_cmd,
                    exec_command=exec_cmd,
                    reasoning="Fast-path: Shell execution prefix"
                )
            elif (cmd.startswith("!분석 ") or cmd.startswith("!analyze ")) and len(cmd.split()) >= 2:
                parts = cmd.split(None, 2)
                cand_repo = parts[1].strip()
                available_repos = list(self.repo_manager.repo_mapping.keys())
                if cand_repo in available_repos or cand_repo.startswith("http"):
                    summary_part = parts[2].strip() if len(parts) > 2 else f"{cand_repo} 분석"
                    intent = IntentAnalysisResult(
                        task_type=TaskType.READ,
                        target_repo=cand_repo,
                        summary=summary_part,
                        query=summary_part,
                        reasoning="Fast-path: Structured read command"
                    )

            if not intent:
                with profiler.step("intent_llm"):
                    available_repos = list(self.repo_manager.repo_mapping.keys())
                    intent = self.intent_analyzer.analyze(
                        raw_text=cmd,
                        title=cmd,
                        available_repos=available_repos
                    )
            logger.info(
                f"[{trace_id}][CONSOLE Intent] Type: {intent.task_type.value} | TargetRepo: {intent.target_repo} | Summary: {intent.summary}"
            )

            self._update_status(
                f"# 🔄 [작업 진행 중 (CONSOLE)] {intent.summary}\n\n"
                f"- Trace ID: `{trace_id}`\n- 시각: {now_str}\n- 대상: {intent.target_repo}\n- 유형: {intent.task_type.value}"
            )

            # 8. Dispatch based on task type
            with profiler.step("repo_prepare"):
                repo_path = self.repo_manager.prepare_repo(intent.target_repo)

            output_str = ""
            history_entry = ""
            action_status: Optional[str] = None
            action_message: Optional[str] = None

            with profiler.step("executor"):
                if intent.task_type == TaskType.READ:
                    rep_folder = self.storage_manager.get_destination_folder("reports") if self.storage_manager else self.folder_id
                    doc_title = self.storage_manager.format_mobile_title(TaskType.READ, intent.target_repo, intent.summary) if self.storage_manager else "CONSOLE_READ"
                    result = self.read_executor.execute(repo_path, intent, original_title=doc_title, parent_id=rep_folder)
                    output_str = (
                        f"### 📄 [분석 보고서 요약]\n"
                        f"- 대상 저장소: `{intent.target_repo}`\n"
                        f"- 분석 주제: **{intent.summary}**\n\n"
                        f"{result.get('preview', '')}\n\n"
                        f"*(전체 보고서는 Google Drive의 `{result.get('doc_name')}` 문서에 저장되었습니다.)*"
                    )
                    history_entry = f"- [{now_str[5:16]}] [📄 분석] {intent.summary} (#{trace_id[-4:]})"
                    if "[Guardrail:" in (intent.reasoning or ""):
                        action_status = "GUARDRAIL_REDIRECT"
                        action_message = "파일 수정 지시가 구체적이지 않아 안전 가드레일에 의해 [분석 보고서]로 자동 전환되었습니다."
                    else:
                        action_status = "READ_SUCCESS"
                        action_message = f"[{intent.target_repo}] {intent.summary} 분석 보고서 생성 완료"

                elif intent.task_type == TaskType.WRITE:
                    result = self.write_executor.execute(repo_path, intent)
                    commit_folder = self.storage_manager.get_destination_folder("commits") if self.storage_manager else self.folder_id
                    doc_title = self.storage_manager.format_mobile_title(TaskType.WRITE, intent.target_repo, result.get('commit_message') or intent.summary) if self.storage_manager else f"[완료] {intent.summary}"
                    self._create_completion_doc(doc_title, result, parent_id=commit_folder)

                    diff_preview = result.get('diff') or '(신규 파일)'
                    if len(diff_preview) > 1200:
                        diff_preview = diff_preview[:1200] + "\n...(생략)..."
                    output_str = (
                        f"### ✅ [코드 변경 및 Git 커밋 완료]\n"
                        f"- 대상 저장소: `{intent.target_repo}`\n"
                        f"- 변경 파일: `{result.get('target_path')}`\n"
                        f"- 커밋 메시지: `{result.get('commit_message')}`\n"
                        f"- 커밋 해시: `{result.get('commit_hash')}` (GitHub origin/main 푸시 완료)\n\n"
                        f"```diff\n{diff_preview}\n```"
                    )
                    history_entry = f"- [{now_str[5:16]}] [✅ 수정] `{result.get('target_path')}`: {result.get('commit_message')} ({result.get('commit_hash', '')[:7]}, #{trace_id[-4:]})"
                    action_status = "COMMIT_SUCCESS"
                    if result.get("is_moved") and result.get("source_path"):
                        action_message = f"`{result.get('source_path')}` ➔ `{result.get('target_path')}` 이동 및 Git 반영 완료 ({result.get('commit_hash', '')[:7]})"
                    else:
                        action_message = f"`{result.get('target_path')}` 변경 및 Git 반영 완료 ({result.get('commit_hash', '')[:7]})"

                elif intent.task_type == TaskType.EXEC:
                    if self._is_janitor_command(intent.exec_command or cmd):
                        output_str = self._execute_janitor_command(intent.exec_command or cmd)
                        history_entry = f"- [{now_str[5:16]}] [🧹 정리] {cmd[:25]} (#{trace_id[-4:]})"
                        action_status = "READ_SUCCESS"
                        action_message = "드라이브 생명주기 및 스토리지 정리 완료"
                    else:
                        log_folder = self.storage_manager.get_destination_folder("logs") if self.storage_manager else self.folder_id
                        doc_title = self.storage_manager.format_mobile_title(TaskType.EXEC, intent.target_repo, intent.summary or intent.exec_command) if self.storage_manager else "CONSOLE_EXEC"
                        result = self.exec_executor.execute(repo_path, intent, original_title=doc_title, parent_id=log_folder)
                        console_out = (result.get('stdout', '') + '\n' + result.get('stderr', '')).strip()
                        if len(console_out) > 1200:
                            console_out = console_out[:1200] + "\n...(생략)..."
                        output_str = (
                            f"### 💻 [명령 실행 완료]\n"
                            f"- 명령어: `{intent.exec_command}`\n"
                            f"- 종료 코드: {result.get('exit_code')}\n\n"
                            f"```text\n{console_out or '(출력 없음)'}\n```"
                        )
                        history_entry = f"- [{now_str[5:16]}] [💻 실행] `{intent.exec_command}` (종료: {result.get('exit_code')}, #{trace_id[-4:]})"
                        action_status = "READ_SUCCESS"
                        action_message = f"명령어 `{intent.exec_command}` 실행 완료 (종료 코드: {result.get('exit_code')})"

            # 9. Update History list (keep recent 3)
            history.insert(0, history_entry)
            history = history[:3]

            duration_summary = profiler.format_summary()
            logger.info(f"[{trace_id}][CONSOLE] Pipeline Completed: {duration_summary} | sync_lag={int(sync_lag_ms)}ms")

            # 10. Render final ONLINE state with result and telemetry
            with profiler.step("drive_update"):
                # Non-Destructive Overwrite Guard:
                # Check if user typed a NEW command while previous task was running
                input_cmd_to_render = DEFAULT_PLACEHOLDER
                try:
                    current_raw = self._read_console_content()
                    if current_raw:
                        current_cmd = ConsoleProtocolParser.extract_command(current_raw)
                        if current_cmd:
                            current_cmd_hash = ConsoleProtocolParser.compute_command_hash(current_cmd)
                            if current_cmd_hash != cmd_hash:
                                logger.info(
                                    f"[{trace_id}][CONSOLE Guard] Preserving new user command typed during execution: '{current_cmd}'"
                                )
                                input_cmd_to_render = current_cmd
                                self._last_activity_time = time.time()
                except Exception as guard_err:
                    logger.warning(f"Could not check CONSOLE before final render: {guard_err}")

                done_text = ConsoleDocFormatter.render(
                    status="ONLINE",
                    input_command=input_cmd_to_render,
                    output_content=output_str,
                    history_items=history,
                    trace_id=trace_id,
                    duration_summary=duration_summary,
                    sync_lag_ms=sync_lag_ms,
                    action_status=action_status,
                    action_message=action_message,
                )
                self._write_console_content(done_text)

            self._last_processed_command_hash = cmd_hash
            self._last_activity_time = time.time()

            self._update_status(
                f"# ✅ [작업 완료 (CONSOLE)] {intent.summary}\n\n"
                f"- Trace ID: `{trace_id}`\n"
                f"- 소요 시간: `{duration_summary}`\n"
                f"- 완료 시각: {TimeTagFormatter.format_kst()}\n"
                f"- 대상 저장소: {intent.target_repo}"
            )

        except Exception as e:
            logger.error(f"[CONSOLE Error] Failed to process CONSOLE task: {e}")
            logger.error(traceback.format_exc())
            self._handle_console_error(e, trace_id=locals().get("trace_id"))

    def _handle_console_error(self, error: Exception, trace_id: Optional[str] = None):
        """Displays friendly error in CONSOLE output area without crashing daemon."""
        if not self.drive_service or not self.console_doc_id:
            return
        try:
            tid_info = f" | **Trace ID**: `{trace_id}`" if trace_id else ""
            error_output = (
                f"### ⚠️ [작업 처리 중 오류 발생]{tid_info}\n"
                f"- 오류 종류: `{type(error).__name__}`\n"
                f"- 오류 메시지: `{str(error)}`\n\n"
                f"시스템은 정상 유지 중입니다. 입력 형식을 확인 후 다시 시도해 주세요."
            )
            error_text = ConsoleDocFormatter.render(
                status="ERROR",
                input_command=DEFAULT_PLACEHOLDER,
                output_content=error_output,
                trace_id=trace_id,
                action_status="ERROR",
                action_message=f"{type(error).__name__}: {str(error)}"
            )
            self._write_console_content(error_text)
        except Exception as err:
            logger.error(f"Failed to write error state to CONSOLE doc: {err}")

    def _sync_task_result_to_console(
        self,
        output_str: str,
        action_status: Optional[str] = None,
        action_message: Optional[str] = None,
        history_entry: Optional[str] = None,
        trace_id: Optional[str] = None,
    ):
        """Syncs completed task document result into CONSOLE doc so mobile Gem can read it immediately."""
        if not self.drive_service or not self.console_doc_id:
            return
        try:
            current_text = self._read_console_content()
            history = ConsoleProtocolParser.extract_history(current_text) if current_text else []
            if history_entry:
                history.insert(0, history_entry)
                history = history[:3]

            # Preserve pending input command if user typed one
            pending_cmd = ConsoleProtocolParser.extract_command(current_text) if current_text else None
            input_cmd_to_render = pending_cmd if pending_cmd else DEFAULT_PLACEHOLDER

            tid = trace_id or f"tsk_{int(time.time())}"
            updated_text = ConsoleDocFormatter.render(
                status="ONLINE",
                input_command=input_cmd_to_render,
                output_content=output_str,
                history_items=history,
                trace_id=tid,
                duration_summary=f"완료: {TimeTagFormatter.format_kst()}",
                action_status=action_status,
                action_message=action_message,
            )
            self._write_console_content(updated_text)
            self._last_processed_command_hash = ConsoleProtocolParser.compute_command_hash(input_cmd_to_render)
            self._last_activity_time = time.time()
            logger.info("Successfully synced task document result to CONSOLE.")
        except Exception as e:
            logger.warning(f"Could not sync task document result to CONSOLE: {e}")

    @staticmethod
    def _is_janitor_command(cmd: str) -> bool:
        """Determines if a command is a maintenance/clean trigger."""
        clean = (cmd or "").strip().lower()
        patterns = [
            "clean logs", "clean trash", "clean drive", "storage stats",
            "드라이브 정리", "로그 정리", "휴지통 비워", "스토리지 통계", "드라이브 통계"
        ]
        return any(p in clean for p in patterns)

    def _execute_janitor_command(self, cmd: str) -> str:
        """Executes maintenance and cleanup commands, returning human-friendly summary text."""
        if not self.janitor:
            return "스토리지 매니저가 초기화되지 않았습니다."
        clean = (cmd or "").strip().lower()
        if "trash" in clean or "휴지통" in clean:
            purged = self.janitor.purge_trash(max_files=50)
            return f"🗑️ **[휴지통 영구 삭제 완료]**\n- 영구 삭제된 파일: {purged}개\n- AI 검색 노이즈 원천 차단 완료."
        elif "stats" in clean or "통계" in clean:
            stats = self.janitor.get_storage_stats()
            return (
                f"📊 **[드라이브 스토리지 현황]**\n"
                f"- 보고서 (reports/): {stats.get('reports', 0)}개\n"
                f"- 커밋 확인서 (commits/): {stats.get('commits', 0)}개\n"
                f"- 실행 로그 (logs/): {stats.get('logs', 0)}개\n"
                f"- 아카이브 (archive/): {stats.get('archive', 0)}개\n"
                f"- 루트 파일 (CONSOLE/STATUS 등): {stats.get('root', 0)}개\n"
                f"- 휴지통 파일: {stats.get('trash', 0)}개"
            )
        else:
            cleaned = self.janitor.clean_expired_documents()
            purged = self.janitor.purge_trash(max_files=30)
            stats = self.janitor.get_storage_stats()
            return (
                f"🧹 **[드라이브 정리 완료]**\n"
                f"- 만료 정리: {cleaned}\n"
                f"- 휴지통 영구 삭제: {purged}개\n"
                f"- 현재 보관 현황: 보고서 {stats.get('reports', 0)}개, 커밋 {stats.get('commits', 0)}개, 로그 {stats.get('logs', 0)}개"
            )

    def find_candidate_documents(self) -> List[dict]:
        """Queries Google Drive for pending task documents, strictly ignoring CONSOLE and STATUS."""
        if not self.drive_service:
            return []

        query = "(mimeType = 'application/vnd.google-apps.document' or mimeType = 'text/plain' or mimeType = 'application/json') and trashed = false"
        try:
            results = self.drive_service.files().list(
                q=query,
                fields="files(id, name, mimeType, parents, createdTime, modifiedTime)",
                pageSize=20,
                orderBy="modifiedTime desc"
            ).execute()
        except Exception as e:
            logger.error(f"Drive files.list error: {e}")
            return []

        raw_files = results.get("files", [])
        candidates = []

        for f in raw_files:
            file_id = f["id"]
            file_name = f.get("name", "")

            # Exclude special system docs by ID or exact name
            if file_id == self.console_doc_id or file_id == self.status_doc_id:
                continue

            if file_name in ("CONSOLE", "STATUS", "[최신결과] CONSOLE"):
                continue

            if any(file_name.startswith(p) for p in self.SYSTEM_DOC_PREFIXES):
                continue

            if file_id in self.processed_ids:
                continue

            # Check trigger keywords or Gemini mobile export prefix
            if file_name.startswith("Gemini -") or any(k.lower() in file_name.lower() for k in self.TRIGGER_KEYWORDS):
                candidates.append(f)

        return candidates

    def process_single_task(
        self,
        doc_id: str,
        doc_name: str,
        doc_mime: str = "application/vnd.google-apps.document",
        parent_id: Optional[str] = None
    ):
        """Processes a single task document through the dispatcher pipeline."""
        logger.info(f"=== [Dispatcher] Task detected: '{doc_name}' (ID: {doc_id}) ===")
        self.processed_ids.add(doc_id)

        try:
            # 1. Export or download document text
            if doc_mime == "application/vnd.google-apps.document":
                raw_text = self.drive_service.files().export_media(
                    fileId=doc_id,
                    mimeType="text/plain"
                ).execute().decode("utf-8")
            else:
                raw_text = self.drive_service.files().get_media(
                    fileId=doc_id
                ).execute().decode("utf-8")

            # 2. Analyze Intent (Enforcing Default=READ)
            available_repos = list(self.repo_manager.repo_mapping.keys())
            intent = self.intent_analyzer.analyze(
                raw_text=raw_text,
                title=doc_name,
                available_repos=available_repos
            )
            logger.info(
                f"[Intent] Type: {intent.task_type.value} | TargetRepo: {intent.target_repo} | Summary: {intent.summary}"
            )

            now_str = time.strftime('%Y-%m-%d %H:%M:%S')
            self._update_status(
                f"# ⏳ [처리 중] {doc_name}\n\n"
                f"- 감지 시각: {now_str} KST\n"
                f"- 대상 저장소: {intent.target_repo}\n"
                f"- 작업 유형: {intent.task_type.value}\n"
                f"- 요약: {intent.summary}\n\n"
                f"현재 WSL 환경에서 작업을 수행하고 있습니다..."
            )

            # 3. Dynamic Repository Preparation
            repo_path = self.repo_manager.prepare_repo(intent.target_repo)
            logger.info(f"[Repo] Prepared repository at: {repo_path}")

            # 4. Dispatch to Executor based on TaskType
            if intent.task_type == TaskType.READ:
                rep_folder = self.storage_manager.get_destination_folder("reports") if self.storage_manager else parent_id
                doc_title = self.storage_manager.format_mobile_title(TaskType.READ, intent.target_repo, intent.summary) if self.storage_manager else doc_name
                result = self.read_executor.execute(repo_path, intent, original_title=doc_title, parent_id=rep_folder)
                logger.info(f"[READ] Generated report doc: {result.get('doc_name')} ({result.get('doc_id')})")
                self._update_status(
                    f"# 📄 [분석 보고서 완료] {doc_name}\n\n"
                    f"- 완료 시각: {time.strftime('%Y-%m-%d %H:%M:%S')} KST\n"
                    f"- 대상 저장소: {intent.target_repo}\n"
                    f"- 분석 주제: {intent.summary}\n\n"
                    f"## 분석 결과 요약\n"
                    f"{result.get('preview', '')}\n\n"
                    f"*(전체 보고서는 구글 드라이브의 '{result.get('doc_name')}' 문서에 저장되었습니다.)*"
                )
                output_str = (
                    f"### 📄 [분석 보고서 요약]\n"
                    f"- 대상 저장소: `{intent.target_repo}`\n"
                    f"- 분석 주제: **{intent.summary}**\n\n"
                    f"{result.get('preview', '')}\n\n"
                    f"*(전체 보고서는 Google Drive의 `{result.get('doc_name')}` 문서에 저장되었습니다.)*"
                )
                self._sync_task_result_to_console(
                    output_str=output_str,
                    action_status="READ_SUCCESS",
                    action_message=f"[{intent.target_repo}] {intent.summary} 분석 보고서 생성 완료",
                    history_entry=f"- [{time.strftime('%m-%d %H:%M')}] [📄 분석] {intent.summary} (#{doc_id[-4:] if doc_id else 'task'})",
                    trace_id=f"tsk_{doc_id[-6:] if doc_id else int(time.time())}",
                )

            elif intent.task_type == TaskType.WRITE:
                result = self.write_executor.execute(repo_path, intent)
                logger.info(
                    f"[WRITE] Committed {result.get('commit_hash', '')[:7]}: {result.get('commit_message')} on {result.get('target_path')}"
                )
                # Upload brief completion confirmation to Google Drive in commits/ folder
                commit_folder = self.storage_manager.get_destination_folder("commits") if self.storage_manager else parent_id
                doc_title = self.storage_manager.format_mobile_title(TaskType.WRITE, intent.target_repo, result.get('commit_message') or intent.summary) if self.storage_manager else doc_name
                self._create_completion_doc(doc_title, result, parent_id=commit_folder)
                self._update_status(
                    f"# ✅ [작업 완료] {doc_name}\n\n"
                    f"- 완료 시각: {time.strftime('%Y-%m-%d %H:%M:%S')} KST\n"
                    f"- 대상 저장소: {intent.target_repo}\n"
                    f"- 변경 파일: `{result.get('target_path')}`\n"
                    f"- 커밋 메시지: `{result.get('commit_message')}`\n"
                    f"- 커밋 해시: `{result.get('commit_hash')}` (GitHub origin/main 푸시 완료)\n\n"
                    f"### 변경 내용 (Diff)\n"
                    f"```diff\n{result.get('diff') or '(신규 파일)'}\n```\n"
                )
                output_str = (
                    f"### 🟢 [작업 완료 및 Git 반영]\n"
                    f"- 대상 저장소: `{intent.target_repo}`\n"
                    f"- 변경 파일: `{result.get('target_path')}`\n"
                    f"- 커밋 메시지: `{result.get('commit_message')}`\n"
                    f"- 커밋 해시: `{result.get('commit_hash')}` (origin/main 푸시 완료)\n\n"
                    f"#### 주요 변경 내용 (Diff)\n"
                    f"```diff\n{result.get('diff') or '(신규 파일)'}\n```"
                )
                self._sync_task_result_to_console(
                    output_str=output_str,
                    action_status="COMMIT_SUCCESS",
                    action_message=f"커밋 `{result.get('commit_hash', '')[:7]}` 완료 ({intent.target_repo})",
                    history_entry=f"- [{time.strftime('%m-%d %H:%M')}] [🟢 커밋] {result.get('commit_message')} (#{doc_id[-4:] if doc_id else 'task'})",
                    trace_id=f"tsk_{doc_id[-6:] if doc_id else int(time.time())}",
                )

            elif intent.task_type == TaskType.EXEC:
                if self._is_janitor_command(intent.exec_command or doc_name):
                    output_str = self._execute_janitor_command(intent.exec_command or doc_name)
                    self._update_status(
                        f"# 🧹 [스토리지 정리 완료] {doc_name}\n\n"
                        f"- 완료 시각: {time.strftime('%Y-%m-%d %H:%M:%S')} KST\n\n"
                        f"{output_str}"
                    )
                    self._sync_task_result_to_console(
                        output_str=output_str,
                        action_status="READ_SUCCESS",
                        action_message="드라이브 생명주기 및 스토리지 정리 완료",
                        history_entry=f"- [{time.strftime('%m-%d %H:%M')}] [🧹 정리] {doc_name[:25]} (#{doc_id[-4:] if doc_id else 'task'})",
                        trace_id=f"tsk_{doc_id[-6:] if doc_id else int(time.time())}",
                    )
                else:
                    log_folder = self.storage_manager.get_destination_folder("logs") if self.storage_manager else parent_id
                    doc_title = self.storage_manager.format_mobile_title(TaskType.EXEC, intent.target_repo, intent.summary or intent.exec_command) if self.storage_manager else doc_name
                    result = self.exec_executor.execute(repo_path, intent, original_title=doc_title, parent_id=log_folder)
                    logger.info(f"[EXEC] Command completed with exit code {result.get('exit_code')}")
                    self._update_status(
                        f"# 💻 [명령 실행 완료] {doc_name}\n\n"
                        f"- 완료 시각: {time.strftime('%Y-%m-%d %H:%M:%S')} KST\n"
                        f"- 명령어: `{intent.exec_command}`\n"
                        f"- 종료 코드: {result.get('exit_code')}\n\n"
                        f"### 실행 콘솔 출력\n"
                        f"```text\n{(result.get('stdout', '') + chr(10) + result.get('stderr', '')).strip() or '(출력 없음)'}\n```\n"
                    )
                    output_str = (
                        f"### 💻 [명령 실행 완료]\n"
                        f"- 대상 저장소: `{intent.target_repo}`\n"
                        f"- 명령어: `{intent.exec_command}`\n"
                        f"- 종료 코드: `{result.get('exit_code')}`\n\n"
                        f"#### 실행 콘솔 출력\n"
                        f"```text\n{(result.get('stdout', '') + chr(10) + result.get('stderr', '')).strip() or '(출력 없음)'}\n```"
                    )
                    self._sync_task_result_to_console(
                        output_str=output_str,
                        action_status="READ_SUCCESS",
                        action_message=f"명령어 `{intent.exec_command}` 실행 완료 (종료 코드: {result.get('exit_code')})",
                        history_entry=f"- [{time.strftime('%m-%d %H:%M')}] [💻 실행] {intent.exec_command} (#{doc_id[-4:] if doc_id else 'task'})",
                        trace_id=f"tsk_{doc_id[-6:] if doc_id else int(time.time())}",
                    )

            # 5. Move original document to trash
            self._trash_document(doc_id, doc_name)

        except Exception as e:
            logger.error(f"[Dispatcher Error] Task '{doc_name}' failed: {e}")
            logger.error(traceback.format_exc())
            self._handle_task_error(doc_id, doc_name, e, parent_id=parent_id)
            self._handle_console_error(e, trace_id=f"err_{doc_id[-6:] if doc_id else int(time.time())}")
            self._trash_document(doc_id, doc_name, error=True)

    def _trash_document(self, doc_id: str, doc_name: str, error: bool = False):
        """Moves processed document to trash to prevent re-processing."""
        if not self.drive_service:
            return
        try:
            self.drive_service.files().update(
                fileId=doc_id,
                body={"trashed": True}
            ).execute()
            status_desc = "Errored task" if error else "Task"
            logger.info(f"[Dispatcher] {status_desc} document moved to trash: '{doc_name}'")
        except Exception as e:
            logger.warning(f"Could not trash document {doc_id} ('{doc_name}'): {e}")

    def _create_completion_doc(self, original_title: str, write_result: dict, parent_id: Optional[str] = None):
        """Creates a readable completion document on Drive."""
        if not self.drive_service:
            return
        if original_title.startswith("[✅커밋:"):
            doc_title = original_title
        else:
            doc_title = f"[완료] {original_title}"
        content = f"""# {doc_title}

## 작업 반영 완료 안내
요청하신 코드 변경 작업이 성공적으로 리포지토리에 반영 및 Push 되었습니다.

- 대상 파일: `{write_result.get('target_path')}`
- 커밋 메시지: `{write_result.get('commit_message')}`
- 커밋 해시: `{write_result.get('commit_hash')}`

### 변경 내용 (Diff)
```diff
{write_result.get('diff') or '(변경 없음 또는 신규 파일)'}
```
"""
        try:
            target_parent = parent_id or (self.storage_manager.get_destination_folder("commits") if self.storage_manager else self.folder_id)
            media = MediaInMemoryUpload(content.encode("utf-8"), mimetype="text/plain", resumable=True)
            body = {"name": doc_title, "mimeType": "application/vnd.google-apps.document"}
            if target_parent:
                body["parents"] = [target_parent]
            self.drive_service.files().create(
                body=body,
                media_body=media,
                fields="id, name"
            ).execute()
        except Exception as e:
            logger.warning(f"Could not upload completion doc: {e}")

    def _handle_task_error(self, doc_id: str, doc_name: str, error: Exception, parent_id: Optional[str] = None):
        """Safely logs error and creates an error document on Google Drive without crashing."""
        if not self.drive_service:
            return

        if self.storage_manager:
            error_title = self.storage_manager.format_mobile_title(TaskType.READ, "error", doc_name, is_error=True)
            target_parent = self.storage_manager.get_destination_folder("logs/errors", auto_monthly=False)
        else:
            error_title = f"[오류] {doc_name}"
            target_parent = parent_id or self.folder_id

        error_md = f"""# [오류 보고서] {doc_name}

작업을 수행하는 도중 예외가 발생하여 중단되었습니다.
시스템은 다운되지 않고 정상 유지 중입니다.

## 1. 발생 에러
`{type(error).__name__}: {str(error)}`

## 2. 상세 트레이스백
```
{traceback.format_exc()}
```

## 3. 조치 제안
- 요청 문서의 대상 리포지토리명 및 명령어 형식을 다시 확인해 주세요.
- 보호된 파일(README.md 등) 수정 시도가 아닌지 확인해 주세요.
"""
        try:
            media = MediaInMemoryUpload(error_md.encode("utf-8"), mimetype="text/plain", resumable=True)
            body = {"name": error_title, "mimeType": "application/vnd.google-apps.document"}
            if target_parent:
                body["parents"] = [target_parent]
            self.drive_service.files().create(
                body=body,
                media_body=media,
                fields="id, name"
            ).execute()
            logger.info(f"Created error notification document on Drive: {error_title}")
            self._update_status(
                f"# ⚠️ [작업 실패] {doc_name}\n\n"
                f"- 실패 시각: {time.strftime('%Y-%m-%d %H:%M:%S')} KST\n"
                f"- 오류: `{type(error).__name__}: {str(error)}`\n\n"
                f"*(상세 내용은 구글 드라이브의 '{error_title}' 문서를 확인해 주세요.)*"
            )
        except Exception as upload_err:
            logger.error(f"Failed to create error document on Drive: {upload_err}")

    def run_poll_cycle(self):
        """Executes a single polling and processing cycle."""
        # 1. Check and process single bi-directional CONSOLE document
        self.check_and_process_console()

        # 2. Check heartbeat (every 5 minutes of idle)
        if time.time() - self._last_heartbeat_time > 300:
            self._update_console_heartbeat()
            self._last_heartbeat_time = time.time()

        # 3. Periodic background janitor cleanup (every 6 hours)
        if self.janitor and (time.time() - self._last_janitor_run_time > 21600):
            try:
                logger.info("[Janitor] Starting scheduled background cleanup cycle...")
                self.janitor.clean_expired_documents()
                self._last_janitor_run_time = time.time()
            except Exception as e:
                logger.warning(f"[Janitor] Scheduled cleanup error: {e}")

        # 4. Check legacy or ephemeral task documents
        candidates = self.find_candidate_documents()
        if candidates:
            self._last_activity_time = time.time()
            logger.info(f"Found {len(candidates)} candidate document(s).")
            for doc in candidates:
                parents = doc.get("parents") or []
                parent_id = parents[0] if parents else None
                self.process_single_task(
                    doc_id=doc["id"],
                    doc_name=doc.get("name", "Untitled"),
                    doc_mime=doc.get("mimeType", "application/vnd.google-apps.document"),
                    parent_id=parent_id
                )

    def start(self):
        """Runs the main polling dispatcher loop."""
        logger.info(f"=== gem-bridge v{__version__} Dispatcher Daemon Started ===")
        logger.info(
            f"Adaptive polling: Active={self.active_poll_interval}s, Idle={self.idle_poll_interval}s | Repositories: {list(self.repo_mapping.keys())}"
        )

        while True:
            try:
                self.run_poll_cycle()
            except Exception as loop_error:
                logger.error(f"Error in daemon polling loop: {loop_error}")
                logger.error(traceback.format_exc())
            time.sleep(self.get_sleep_interval())


def main():
    if "--once" in sys.argv:
        daemon = GemBridgeDaemonV2()
        daemon.run_poll_cycle()
    else:
        daemon = GemBridgeDaemonV2()
        daemon.start()


if __name__ == "__main__":
    main()
