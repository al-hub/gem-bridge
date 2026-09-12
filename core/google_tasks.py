import logging
import time
from typing import Any, Dict, List, Optional
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError

logger = logging.getLogger("gem_bridge.google_tasks")

TASKS_SCOPE = "https://www.googleapis.com/auth/tasks"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
COMBINED_SCOPES = [DRIVE_SCOPE, TASKS_SCOPE]


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
            # Filter only tasks with 'needsAction' status and valid title
            pending = [
                item for item in items
                if item.get("status") == "needsAction" and item.get("title", "").strip()
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
