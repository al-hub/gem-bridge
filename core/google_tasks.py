import logging
import time
from typing import Any, Dict, List, Optional
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError

logger = logging.getLogger("gem_bridge.google_tasks")

TASKS_SCOPE = "https://www.googleapis.com/auth/tasks"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
COMBINED_SCOPES = [DRIVE_SCOPE, TASKS_SCOPE]

PROCESSED_PREFIXES = ("[✅완료", "[❌오류", "[⏳진행", "[✅", "[❌")
MAX_NOTES_LENGTH = 8000


class GoogleTasksManager:
    """
    Manager for interacting with the Google Tasks API.
    Enables zero-touch (0-Tap) mobile task ingestion from Gemini Mobile (@Google Tasks).
    """

    def __init__(self, credentials: Optional[Any] = None):
        self.credentials = credentials
        self.service: Optional[Resource] = None
        self._is_available: bool = False
        self._init_service()

    def _init_service(self):
        """Attempts to initialize the Google Tasks API client."""
        if not self.credentials:
            logger.info("Google Tasks: No credentials provided.")
            self._is_available = False
            return

        try:
            # Check if credentials have the tasks scope
            scopes = getattr(self.credentials, "scopes", []) or []
            has_tasks_scope = any("tasks" in s for s in scopes)
            if not has_tasks_scope:
                logger.warning(
                    "Google Tasks: Existing credentials lack 'https://www.googleapis.com/auth/tasks' scope. "
                    "Run auth_helper.py to authorize Google Tasks."
                )
                self._is_available = False
                return

            self.service = build("tasks", "v1", credentials=self.credentials, cache_discovery=False)
            self._is_available = True
            logger.info("Google Tasks API service successfully initialized.")
        except Exception as e:
            logger.warning(f"Google Tasks API initialization failed: {e}")
            self.service = None
            self._is_available = False

    @property
    def is_available(self) -> bool:
        return self._is_available and self.service is not None

    def list_pending_tasks(
        self,
        tasklist_id: str = "@default",
        max_results: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Retrieves pending tasks (status='needsAction') from the specified tasklist.
        """
        if not self.is_available or not self.service:
            return []

        try:
            results = self.service.tasks().list(
                tasklist=tasklist_id,
                showCompleted=False,
                showHidden=False,
                maxResults=max_results
            ).execute()
            items = results.get("items", [])
            # Filter only tasks with 'needsAction' status, valid title, and not already processed
            pending = [
                item for item in items
                if item.get("status") == "needsAction"
                and item.get("title", "").strip()
                and not any(item.get("title", "").strip().startswith(prefix) for prefix in PROCESSED_PREFIXES)
            ]
            return pending
        except HttpError as e:
            if e.resp.status == 403:
                logger.warning(f"Google Tasks permission error (403): {e}")
                self._is_available = False
            else:
                logger.error(f"Google Tasks API HTTP error: {e}")
            return []
        except Exception as e:
            logger.error(f"Failed to list Google Tasks: {e}")
            return []

    def complete_task(
        self,
        task_id: str,
        tasklist_id: str = "@default",
        completion_notes: Optional[str] = None
    ) -> bool:
        """
        Marks a task as completed and optionally updates its notes with completion details.
        """
        if not self.is_available or not self.service:
            return False

        try:
            # Fetch existing task to preserve existing notes if any
            task = self.service.tasks().get(tasklist=tasklist_id, task=task_id).execute()
            existing_notes = task.get("notes", "") or ""

            body: Dict[str, Any] = {
                "id": task_id,
                "status": "completed",
                "completed": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
            }

            if completion_notes:
                combined_notes = f"{existing_notes}\n\n{completion_notes}".strip()
                body["notes"] = combined_notes

            self.service.tasks().patch(
                tasklist=tasklist_id,
                task=task_id,
                body=body
            ).execute()
            logger.info(f"Google Tasks: Successfully completed task '{task_id}'.")
            return True
        except Exception as e:
            logger.error(f"Failed to complete Google Task '{task_id}': {e}")
            return False

    def update_task_with_feedback(
        self,
        task_id: str,
        is_success: bool,
        title: str,
        feedback_notes: str,
        tasklist_id: str = "@default"
    ) -> bool:
        """
        Updates task title with [✅완료] or [❌오류] prefix, sets rich feedback notes,
        and keeps status='needsAction' so mobile Gemini can list and brief it directly.
        """
        if not self.is_available or not self.service:
            return False

        try:
            clean_title = title.strip()
            if clean_title.startswith("[✅") or clean_title.startswith("[❌"):
                new_title = clean_title[:1024]
            else:
                for p in PROCESSED_PREFIXES:
                    if clean_title.startswith(p):
                        if "]" in clean_title:
                            clean_title = clean_title.split("]", 1)[1].strip()
                        else:
                            clean_title = clean_title[len(p):].strip()
                prefix = "[✅완료]" if is_success else "[❌오류]"
                new_title = f"{prefix} {clean_title}"[:1024]

            # Fetch existing task to preserve prior notes if any
            existing_notes = ""
            try:
                task = self.service.tasks().get(tasklist=tasklist_id, task=task_id).execute()
                existing_notes = task.get("notes", "") or ""
            except Exception:
                pass

            # Combine and sanitize notes to plain text without Markdown markup
            full_notes = f"{existing_notes}\n\n{feedback_notes}".strip() if existing_notes else feedback_notes.strip()
            if len(full_notes) > MAX_NOTES_LENGTH:
                full_notes = full_notes[:MAX_NOTES_LENGTH - 30] + "\n...(내용 일부 생략)..."

            body: Dict[str, Any] = {
                "id": task_id,
                "title": new_title,
                "notes": full_notes,
                "status": "needsAction"
            }

            self.service.tasks().patch(
                tasklist=tasklist_id,
                task=task_id,
                body=body
            ).execute()
            logger.info(f"Google Tasks: Successfully updated task '{task_id}' with feedback title '{new_title}'.")
            return True
        except Exception as e:
            logger.error(f"Failed to update Google Task '{task_id}' with feedback: {e}")
            return False

    def archive_stale_tasks(
        self,
        max_age_hours: int = 24,
        tasklist_id: str = "@default"
    ) -> int:
        """
        Archives tasks that have been in [✅완료] or [❌오류] state for more than max_age_hours
        by transitioning them from needsAction to completed.
        """
        if not self.is_available or not self.service:
            return 0

        archived_count = 0
        try:
            results = self.service.tasks().list(
                tasklist=tasklist_id,
                showCompleted=False,
                showHidden=False,
                maxResults=100
            ).execute()
            items = results.get("items", [])
            cutoff_epoch = time.time() - (max_age_hours * 3600)

            for item in items:
                title = item.get("title", "")
                if any(title.startswith(p) for p in ("[✅완료", "[❌오류", "[✅", "[❌")):
                    updated_str = item.get("updated", "")
                    try:
                        from datetime import datetime
                        if updated_str.endswith("Z"):
                            updated_str = updated_str[:-1] + "+00:00"
                        updated_dt = datetime.fromisoformat(updated_str)
                        if updated_dt.timestamp() < cutoff_epoch:
                            task_id = item["id"]
                            self.service.tasks().patch(
                                tasklist=tasklist_id,
                                task=task_id,
                                body={
                                    "id": task_id,
                                    "status": "completed",
                                    "completed": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
                                }
                            ).execute()
                            archived_count += 1
                            logger.info(f"Google Tasks: Auto-archived stale completed task '{task_id}' ({title}).")
                    except Exception as parse_err:
                        logger.debug(f"Could not parse task updated time '{updated_str}': {parse_err}")
            return archived_count
        except Exception as e:
            logger.error(f"Failed to archive stale Google Tasks: {e}")
            return archived_count
