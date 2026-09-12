import json
import logging
import os
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
    gem-bridge v2 Dispatcher Daemon.
    Polls Google Drive for incoming task documents, determines intent,
    dynamically prepares repositories, and dispatches to appropriate executors.
    """

    SYSTEM_DOC_PREFIXES = ["[보고서]", "[완료]", "[오류]", "[실행결과]"]
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

    def find_candidate_documents(self) -> List[dict]:
        """Queries Google Drive for pending task documents."""
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

            # Ignore already processed files in memory
            if file_id in self.processed_ids:
                continue

            # Ignore system generated docs
            if any(file_name.startswith(p) for p in self.SYSTEM_DOC_PREFIXES):
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

            # 3. Dynamic Repository Preparation
            repo_path = self.repo_manager.prepare_repo(intent.target_repo)
            logger.info(f"[Repo] Prepared repository at: {repo_path}")

            # 4. Dispatch to Executor based on TaskType
            if intent.task_type == TaskType.READ:
                result = self.read_executor.execute(repo_path, intent, original_title=doc_name, parent_id=parent_id)
                logger.info(f"[READ] Generated report doc: {result.get('doc_name')} ({result.get('doc_id')})")

            elif intent.task_type == TaskType.WRITE:
                result = self.write_executor.execute(repo_path, intent)
                logger.info(
                    f"[WRITE] Committed {result.get('commit_hash', '')[:7]}: {result.get('commit_message')} on {result.get('target_path')}"
                )
                # Upload brief completion confirmation to Google Drive
                self._create_completion_doc(doc_name, result, parent_id=parent_id)

            elif intent.task_type == TaskType.EXEC:
                result = self.exec_executor.execute(repo_path, intent, original_title=doc_name, parent_id=parent_id)
                logger.info(f"[EXEC] Command completed with exit code {result.get('exit_code')}")

            else:
                raise ValueError(f"Unknown TaskType encountered: {intent.task_type}")

            # 5. Move original task document to trash
            self.drive_service.files().update(fileId=doc_id, body={"trashed": True}).execute()
            logger.info(f"[Dispatcher] Task document moved to trash: '{doc_name}'")

        except Exception as task_error:
            logger.error(f"[Dispatcher Error] Task '{doc_name}' failed: {task_error}")
            logger.error(traceback.format_exc())
            self._handle_task_error(doc_id, doc_name, task_error, parent_id=parent_id)
            try:
                self.drive_service.files().update(fileId=doc_id, body={"trashed": True}).execute()
                logger.info(f"[Dispatcher] Errored task document moved to trash: '{doc_name}'")
            except Exception as trash_err:
                logger.warning(f"Could not trash errored task '{doc_name}': {trash_err}")

    def _create_completion_doc(self, original_title: str, write_result: dict, parent_id: Optional[str] = None):
        """Creates a completion confirmation document on Google Drive for WRITE tasks."""
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

## 3. 권장 조치
- `ProtectedFileError`: 보호된 핵심 파일(README, ARCHITECTURE 등) 덮어쓰기가 방지되었습니다. 대상 파일 경로를 확인하세요.
- `RepoError`: 지정된 저장소 경로 또는 URL 접근 권한을 확인하세요.
- `PermissionError`: 파일 및 디렉토리 권한을 확인하세요.
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
        except Exception as upload_err:
            logger.error(f"Failed to create error document on Drive: {upload_err}")

    def run_poll_cycle(self):
        """Executes a single polling and processing cycle."""
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
