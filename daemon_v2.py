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
from core.intent_analyzer import IntentAnalyzer, TaskType
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

    SYSTEM_DOC_PREFIXES = ["[보고서]", "[완료]", "[오류]", "[실행결과]", "STATUS", "CONSOLE"]
    TRIGGER_KEYWORDS = ["!", "깃", "task", "작업", "분석", "실행", "gem-bridge", "보고서"]

    def __init__(self, config: Optional[dict] = None):
        self.config = config or load_config()
        self.poll_interval = self.config.get("poll_interval_seconds", 5)
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
            protected_patterns=self.config.get("protected_files")
        )
        self.exec_executor = ExecExecutor(drive_service=self.drive_service)

        self.processed_ids: Set[str] = set()

        # CONSOLE and STATUS doc tracking
        self.folder_id: Optional[str] = self._find_or_create_folder()
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
        """Finds or creates the GeminiBridge/CONSOLE doc and marks it ONLINE."""
        if not self.drive_service:
            return None
        try:
            q = "name = 'CONSOLE' and trashed = false"
            if self.folder_id:
                q += f" and '{self.folder_id}' in parents"
            res = self.drive_service.files().list(q=q, fields="files(id, name, modifiedTime)").execute()
            files = res.get("files", [])

            if files:
                doc_id = files[0]["id"]
                self.console_doc_id = doc_id
                # Read existing to preserve history/command, then update badge to ONLINE
                try:
                    raw_content = self.drive_service.files().export_media(
                        fileId=doc_id, mimeType="text/plain"
                    ).execute().decode("utf-8")
                    cmd = ConsoleProtocolParser.extract_command(raw_content) or DEFAULT_PLACEHOLDER
                    history = ConsoleProtocolParser.extract_history(raw_content)
                    online_text = ConsoleDocFormatter.render(
                        status="ONLINE",
                        input_command=cmd,
                        output_content="*(PC 데몬이 정상 가동되었습니다. 위 입력창에 작업을 입력하세요.)*",
                        history_items=history
                    )
                    self._write_console_content(online_text)
                    self._last_console_content_hash = ConsoleProtocolParser.compute_content_hash(online_text)
                    logger.info(f"Loaded existing GeminiBridge/CONSOLE doc: {doc_id} (Marked ONLINE)")
                except Exception as read_err:
                    logger.warning(f"Could not read/update existing CONSOLE doc: {read_err}")
                return doc_id

            # Create new CONSOLE doc
            initial_text = ConsoleDocFormatter.render(
                status="ONLINE",
                input_command=DEFAULT_PLACEHOLDER,
                output_content="*(gem-bridge v2 시스템이 시작되었습니다. 위 입력창에 작업을 입력하세요.)*"
            )
            media = MediaInMemoryUpload(initial_text.encode("utf-8"), mimetype="text/plain")
            body = {"name": "CONSOLE", "mimeType": "application/vnd.google-apps.document"}
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
            logger.info(f"Created new GeminiBridge/CONSOLE doc: {doc_id}")
            return doc_id
        except Exception as e:
            logger.warning(f"Could not initialize CONSOLE document: {e}")
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

            logger.info(f"=== [CONSOLE] New command detected: '{cmd}' ===")
            now_str = time.strftime("%Y-%m-%d %H:%M:%S")

            # Extract existing history before processing
            history = ConsoleProtocolParser.extract_history(raw_content)

            # 6. Set PROCESSING status badge
            proc_text = ConsoleDocFormatter.render(
                status="PROCESSING",
                input_command=cmd,
                output_content="*(현재 작업을 수행하고 있습니다. 잠시만 기다려 주세요...)*",
                history_items=history
            )
            self._write_console_content(proc_text)

            # 7. Analyze Intent
            available_repos = list(self.repo_manager.repo_mapping.keys())
            intent = self.intent_analyzer.analyze(
                raw_text=cmd,
                title=cmd,
                available_repos=available_repos
            )
            logger.info(
                f"[CONSOLE Intent] Type: {intent.task_type.value} | TargetRepo: {intent.target_repo} | Summary: {intent.summary}"
            )

            self._update_status(
                f"# 🔄 [작업 진행 중 (CONSOLE)] {intent.summary}\n\n"
                f"- 시각: {now_str} KST\n- 대상: {intent.target_repo}\n- 유형: {intent.task_type.value}"
            )

            # 8. Dispatch based on task type
            repo_path = self.repo_manager.prepare_repo(intent.target_repo)
            output_str = ""
            history_entry = ""

            if intent.task_type == TaskType.READ:
                result = self.read_executor.execute(repo_path, intent, original_title="CONSOLE_READ", parent_id=self.folder_id)
                output_str = (
                    f"### 📄 [분석 보고서 요약]\n"
                    f"- 대상 저장소: `{intent.target_repo}`\n"
                    f"- 분석 주제: **{intent.summary}**\n\n"
                    f"{result.get('preview', '')}\n\n"
                    f"*(전체 보고서는 Google Drive의 `{result.get('doc_name')}` 문서에 저장되었습니다.)*"
                )
                history_entry = f"- [{now_str[:16]} KST] [📄 분석] {intent.summary} (`{intent.target_repo}`)"

            elif intent.task_type == TaskType.WRITE:
                result = self.write_executor.execute(repo_path, intent)
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
                history_entry = f"- [{now_str[:16]} KST] [✅ 수정] `{result.get('target_path')}`: {result.get('commit_message')} ({result.get('commit_hash', '')[:7]})"

            elif intent.task_type == TaskType.EXEC:
                result = self.exec_executor.execute(repo_path, intent, original_title="CONSOLE_EXEC", parent_id=self.folder_id)
                console_out = (result.get('stdout', '') + '\n' + result.get('stderr', '')).strip()
                if len(console_out) > 1200:
                    console_out = console_out[:1200] + "\n...(생략)..."
                output_str = (
                    f"### 💻 [명령 실행 완료]\n"
                    f"- 명령어: `{intent.exec_command}`\n"
                    f"- 종료 코드: {result.get('exit_code')}\n\n"
                    f"```text\n{console_out or '(출력 없음)'}\n```"
                )
                history_entry = f"- [{now_str[:16]} KST] [💻 실행] `{intent.exec_command}` (종료 코드: {result.get('exit_code')})"

            # 9. Update History list (keep recent 3)
            history.insert(0, history_entry)
            history = history[:3]

            # 10. Render final ONLINE state with result
            done_text = ConsoleDocFormatter.render(
                status="ONLINE",
                input_command=DEFAULT_PLACEHOLDER,
                output_content=output_str,
                history_items=history
            )
            self._write_console_content(done_text)

            self._last_processed_command_hash = cmd_hash
            logger.info(f"=== [CONSOLE] Task completed successfully: '{cmd}' ===")

            self._update_status(
                f"# ✅ [작업 완료 (CONSOLE)] {intent.summary}\n\n"
                f"- 완료 시각: {time.strftime('%Y-%m-%d %H:%M:%S')} KST\n"
                f"- 대상 저장소: {intent.target_repo}"
            )

        except Exception as e:
            logger.error(f"[CONSOLE Error] Failed to process CONSOLE task: {e}")
            logger.error(traceback.format_exc())
            self._handle_console_error(e)

    def _handle_console_error(self, error: Exception):
        """Displays friendly error in CONSOLE output area without crashing daemon."""
        if not self.drive_service or not self.console_doc_id:
            return
        try:
            error_output = (
                f"### ⚠️ [작업 처리 중 오류 발생]\n"
                f"- 오류 종류: `{type(error).__name__}`\n"
                f"- 오류 메시지: `{str(error)}`\n\n"
                f"시스템은 정상 유지 중입니다. 입력 형식을 확인 후 다시 시도해 주세요."
            )
            error_text = ConsoleDocFormatter.render(
                status="ERROR",
                input_command=DEFAULT_PLACEHOLDER,
                output_content=error_output
            )
            self._write_console_content(error_text)
        except Exception as err:
            logger.error(f"Failed to write error state to CONSOLE doc: {err}")

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

            # Exclude special system docs by ID or name
            if file_id == self.console_doc_id or file_id == self.status_doc_id:
                continue

            if any(file_name.startswith(p) for p in self.SYSTEM_DOC_PREFIXES):
                continue

            if file_id in self.processed_ids:
                continue

            # Check trigger keywords
            if any(k.lower() in file_name.lower() for k in self.TRIGGER_KEYWORDS):
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
                result = self.read_executor.execute(repo_path, intent, original_title=doc_name, parent_id=parent_id)
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

            elif intent.task_type == TaskType.WRITE:
                result = self.write_executor.execute(repo_path, intent)
                logger.info(
                    f"[WRITE] Committed {result.get('commit_hash', '')[:7]}: {result.get('commit_message')} on {result.get('target_path')}"
                )
                # Upload brief completion confirmation to Google Drive
                self._create_completion_doc(doc_name, result, parent_id=parent_id)
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

            elif intent.task_type == TaskType.EXEC:
                result = self.exec_executor.execute(repo_path, intent, original_title=doc_name, parent_id=parent_id)
                logger.info(f"[EXEC] Command completed with exit code {result.get('exit_code')}")
                self._update_status(
                    f"# 💻 [명령 실행 완료] {doc_name}\n\n"
                    f"- 완료 시각: {time.strftime('%Y-%m-%d %H:%M:%S')} KST\n"
                    f"- 명령어: `{intent.exec_command}`\n"
                    f"- 종료 코드: {result.get('exit_code')}\n\n"
                    f"### 실행 콘솔 출력\n"
                    f"```text\n{(result.get('stdout', '') + chr(10) + result.get('stderr', '')).strip() or '(출력 없음)'}\n```\n"
                )

            # 5. Move original document to trash
            self._trash_document(doc_id, doc_name)

        except Exception as e:
            logger.error(f"[Dispatcher Error] Task '{doc_name}' failed: {e}")
            logger.error(traceback.format_exc())
            self._handle_task_error(doc_id, doc_name, e, parent_id=parent_id)
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
            media = MediaInMemoryUpload(content.encode("utf-8"), mimetype="text/plain", resumable=True)
            body = {"name": doc_title, "mimeType": "application/vnd.google-apps.document"}
            if parent_id:
                body["parents"] = [parent_id]
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

        error_title = f"[오류] {doc_name}"
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
            if parent_id:
                body["parents"] = [parent_id]
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

        # 3. Check legacy or ephemeral task documents
        candidates = self.find_candidate_documents()
        if candidates:
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
        logger.info(f"Polling interval: {self.poll_interval}s | Repositories: {list(self.repo_mapping.keys())}")

        while True:
            try:
                self.run_poll_cycle()
            except Exception as loop_error:
                logger.error(f"Error in daemon polling loop: {loop_error}")
                logger.error(traceback.format_exc())
            time.sleep(self.poll_interval)


def main():
    if "--once" in sys.argv:
        daemon = GemBridgeDaemonV2()
        daemon.run_poll_cycle()
    else:
        daemon = GemBridgeDaemonV2()
        daemon.start()


if __name__ == "__main__":
    main()
