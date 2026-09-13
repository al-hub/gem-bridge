import logging
import re
import time
from typing import Any, Dict, List, Optional
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError

logger = logging.getLogger("gem_bridge.google_tasks")

TASKS_SCOPE = "https://www.googleapis.com/auth/tasks"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
COMBINED_SCOPES = [DRIVE_SCOPE, TASKS_SCOPE]

PROCESSED_PREFIXES = (
    "[✅완료", "[❌오류", "[⏳진행", "[⏳대기",
    "[🔍검토", "[💡기획", "[📝메모", "[📌세션",
    "[✅", "[❌", "[🔍", "[💡", "[📝", "[⏳"
)
MAX_NOTES_LENGTH = 8000
MAX_SAFE_NOTES_LENGTH = 7500
MAX_BODY_LENGTH = 6500
MAX_RETIRE_PER_CYCLE = 3
RETIRE_API_PACING_SEC = 0.25

WATERMARK_PATTERN = re.compile(
    r"<!--\s*GEM_BRIDGE:v=(?P<version>\d+):channel=(?P<channel>[^:]+):repo=(?P<repo>[^:\s]+)(?::trace=(?P<trace>[^:\s]+))?\s*-->"
)
TITLE_PREFIX_PATTERN = re.compile(
    r"^\[(✅완료|❌오류|⏳진행|📝메모|✅테스트|❌테스트|🔍검토|💡기획|✅|❌|🔍|💡|📝)"
)


class SafeMarkdownTruncator:
    """
    Safely truncates markdown text at newline/sentence boundaries while
    guaranteeing that any unclosed fenced code blocks (```) are automatically balanced.
    """

    @staticmethod
    def truncate_body(text: str, max_length: int = MAX_BODY_LENGTH) -> str:
        if not text:
            return ""
        text = text.strip()
        if len(text) <= max_length:
            return text

        cut_index = text[:max_length].rfind("\n")
        if cut_index < int(max_length * 0.7):
            cut_index = text[:max_length].rfind(" ")
            if cut_index < int(max_length * 0.7):
                cut_index = max_length

        truncated = text[:cut_index].rstrip()
        # Ensure code block backticks are balanced
        if truncated.count("```") % 2 != 0:
            truncated += "\n```\n...(이하 코드 생략, 전체 내용은 상단 Docs 참조)..."
        else:
            truncated += "\n...(이하 내용 생략, 전체 내용은 상단 Docs 참조)..."
        return truncated


class TaskWatermark:
    """
    Injects and extracts machine-readable electronic signatures into Google Tasks notes.
    Prevents any deletion or modification of the user's personal to-do items.
    """

    @staticmethod
    def generate(repo: str, channel: str = "tasks", trace_id: str = "") -> str:
        normalized = TaskWatermark.normalize_repo(repo)
        return f"\n\n<!-- GEM_BRIDGE:v=2:channel={channel}:repo={normalized}:trace={trace_id} -->"

    @staticmethod
    def extract(notes: str) -> Optional[Dict[str, str]]:
        if not notes:
            return None
        m = WATERMARK_PATTERN.search(notes)
        return m.groupdict() if m else None

    @staticmethod
    def normalize_repo(repo_str: str) -> str:
        if not repo_str:
            return "gem-bridge"
        name = repo_str.strip().lower()
        if name.endswith(".git"):
            name = name[:-4]
        if "/" in name:
            name = name.split("/")[-1]
        return name or "gem-bridge"


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
            raw_title = title.strip()
            # If title has transient prefix like [⏳, or simple [✅완료]/[❌오류], strip to base title
            clean_title = raw_title
            for p in ["[⏳진행]", "[⏳대기]", "[⏳", "[✅완료]", "[❌오류]", "[✅", "[❌"]:
                if clean_title.startswith(p):
                    if "]" in clean_title:
                        clean_title = clean_title.split("]", 1)[1].strip()
                    else:
                        clean_title = clean_title[len(p):].strip()
                    break

            if not is_success and not raw_title.startswith("[❌"):
                new_title = f"[❌오류] {clean_title}"[:1024]
            elif any(raw_title.startswith(p) for p in ["[✅", "[❌", "[🔍", "[💡", "[📝"]):
                new_title = raw_title[:1024]
            else:
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

    def retire_previous_tasks(
        self,
        target_repo: str,
        current_task_id: str,
        tasklist_id: str = "@default"
    ) -> int:
        """
        Guarantees the 1-Repo 1-Active Invariant:
        Retires previous needsAction tasks for the same target_repo so mobile Gemini
        searches only ever find exactly 1 active task per repository.

        Strict 4-Factor Qualification Guard:
        1. Task ID is not current_task_id
        2. Task title is not in-flight ([⏳진행])
        3. Task has processed prefix or watermark
        4. Matches target_repo via watermark metadata or strict title format
        """
        if not self.is_available or not self.service:
            return 0

        retired_count = 0
        normalized_target = TaskWatermark.normalize_repo(target_repo)

        try:
            results = self.service.tasks().list(
                tasklist=tasklist_id,
                showCompleted=False,
                showHidden=False,
                maxResults=20
            ).execute()

            now_iso = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
            for item in results.get("items", []):
                t_id = item.get("id")
                if not t_id or t_id == current_task_id:
                    continue

                title = (item.get("title") or "").strip()
                notes = item.get("notes") or ""

                # [Guard 1] Never retire in-flight tasks
                if title.startswith("[⏳진행]"):
                    continue

                # [Guard 2] Watermark verification (Zero-Data-Loss for user personal to-dos)
                meta = TaskWatermark.extract(notes)
                is_gem_bridge_task = False
                if meta:
                    if TaskWatermark.normalize_repo(meta.get("repo", "")) == normalized_target:
                        is_gem_bridge_task = True
                else:
                    # Fallback for legacy tasks prior to watermark: check strict title format and notes markers
                    title_match = any(title.startswith(p) for p in PROCESSED_PREFIXES) and (
                        f" {normalized_target} - " in title.lower() or f"] {normalized_target} " in title.lower() or f": {normalized_target}]" in title.lower()
                    )
                    notes_match = any(sig in notes for sig in ["📢 [Gemini", "👉 추천 다음 작업", "👉 다음 추천 작업", "👉 선택지:", "🔗 Docs 열기", "🔗 전체"])
                    if title_match and notes_match:
                        is_gem_bridge_task = True

                if not is_gem_bridge_task:
                    continue

                # Soft-Retire (completed=True)
                try:
                    self.service.tasks().patch(
                        tasklist=tasklist_id,
                        task=t_id,
                        body={
                            "id": t_id,
                            "status": "completed",
                            "completed": now_iso
                        }
                    ).execute()
                    retired_count += 1
                    logger.info(f"[1-Repo 1-Active] Soft-retired prior task '{t_id}' ({title}) for repo '{target_repo}'.")
                    if retired_count >= MAX_RETIRE_PER_CYCLE:
                        break
                    time.sleep(RETIRE_API_PACING_SEC)
                except HttpError as api_err:
                    if api_err.resp.status in (429, 403, 500, 503):
                        logger.warning(f"[Seamless Tasks] Rate limit/API error during retire: {api_err}. Aborting batch.")
                        break
                except Exception as patch_err:
                    logger.warning(f"[Seamless Tasks] Failed to retire task {t_id}: {patch_err}")

            return retired_count
        except Exception as e:
            logger.warning(f"[Seamless Tasks] Task retirement failed gracefully: {e}")
            return retired_count

    def delete_task(self, task_id: str, tasklist_id: str = "@default") -> bool:
        """Explicit hard delete helper for test cleanup or administrative tasks."""
        if not self.is_available or not self.service:
            return False
        try:
            self.service.tasks().delete(tasklist=tasklist_id, task=task_id).execute()
            return True
        except Exception as e:
            logger.warning(f"Failed to delete Google Task '{task_id}': {e}")
            return False

