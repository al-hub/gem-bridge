"""
core/drive_storage.py - Google Drive Hierarchical Folder & Naming Manager for gem-bridge
Manages structured subfolders (reports/, commits/, logs/, archive/) with in-memory caching
and enforces mobile-first naming conventions.
"""

import logging
import re
import time
from typing import Dict, Optional
from core.intent_analyzer import TaskType

logger = logging.getLogger("gem_bridge.drive_storage")


class DriveStorageManager:
    """
    Manages Google Drive folder hierarchy, lazy folder creation,
    ID caching, and document destination routing.
    """

    FOLDER_CATEGORIES = ("reports", "commits", "logs", "logs/errors", "archive")

    def __init__(self, drive_service, root_folder_id: str):
        self.drive_service = drive_service
        self.root_folder_id = root_folder_id
        self._folder_cache: Dict[str, str] = {}  # e.g. "reports" -> folder_id, "reports/2026-09" -> folder_id

    def get_destination_folder(self, category: str, auto_monthly: bool = True) -> str:
        """
        Returns the Google Drive folder ID for the given category.
        If auto_monthly is True, nests under YYYY-MM (e.g. reports/2026-09).
        Caches IDs to prevent unnecessary API queries.
        """
        if not self.drive_service or not self.root_folder_id:
            return self.root_folder_id

        category_clean = category.strip().strip("/")
        current_month = time.strftime("%Y-%m")
        path_key = f"{category_clean}/{current_month}" if auto_monthly else category_clean

        if path_key in self._folder_cache:
            return self._folder_cache[path_key]

        try:
            # Handle nested paths like "logs/errors"
            parts = category_clean.split("/")
            parent_id = self.root_folder_id
            for part in parts:
                parent_id = self._find_or_create_subfolder(parent_id, part)

            if auto_monthly:
                final_folder_id = self._find_or_create_subfolder(parent_id, current_month)
            else:
                final_folder_id = parent_id

            self._folder_cache[path_key] = final_folder_id
            return final_folder_id
        except Exception as e:
            logger.warning(f"[DriveStorage] Failed to resolve subfolder for '{category}': {e}. Falling back to root.")
            return self.root_folder_id

    def _find_or_create_subfolder(self, parent_id: str, folder_name: str) -> str:
        """Finds existing subfolder by name under parent_id, or creates it."""
        cache_key = f"{parent_id}:{folder_name}"
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]

        q = (
            f"mimeType = 'application/vnd.google-apps.folder' "
            f"and name = '{folder_name}' "
            f"and '{parent_id}' in parents "
            f"and trashed = false"
        )
        res = self.drive_service.files().list(q=q, fields="files(id, name)").execute()
        files = res.get("files", [])
        if files:
            folder_id = files[0]["id"]
            self._folder_cache[cache_key] = folder_id
            return folder_id

        # Create new subfolder
        meta = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id]
        }
        folder = self.drive_service.files().create(body=meta, fields="id").execute()
        folder_id = folder["id"]
        self._folder_cache[cache_key] = folder_id
        logger.info(f"[DriveStorage] Created subfolder '{folder_name}' (ID: {folder_id}) under parent {parent_id}")
        return folder_id

    @staticmethod
    def format_mobile_title(
        task_type: TaskType,
        target_repo: str,
        summary: str,
        is_error: bool = False,
        timestamp: Optional[float] = None
    ) -> str:
        """
        Formats document titles according to the Mobile-First Naming convention:
        [{TYPE_ICON}{TYPE_TAG}:{REPO_SHORT}] {CLEAN_SUMMARY} ({YYMMDD_HHmm})
        Ensures critical identification info is within the first 22~28 characters.
        """
        ts = timestamp or time.time()
        date_suffix = time.strftime("%y%m%d_%H%M", time.localtime(ts))

        # 1. Type icon & tag
        if is_error:
            type_tag = "⚠️오류"
        elif task_type == TaskType.READ:
            type_tag = "📄분석"
        elif task_type == TaskType.WRITE:
            type_tag = "✅커밋"
        elif task_type == TaskType.EXEC:
            type_tag = "💻실행"
        else:
            type_tag = "📌작업"

        # 2. Repo short name (strip URLs, owners, path prefixes)
        repo_clean = target_repo.strip().rstrip("/")
        if "/" in repo_clean:
            repo_clean = repo_clean.split("/")[-1]
        if repo_clean.endswith(".git"):
            repo_clean = repo_clean[:-4]
        repo_short = repo_clean[:10] if repo_clean else "bridge"

        # 3. Clean summary: strip redundant prefixes like "!작업", "!분석", "[보고서]"
        clean_sum = summary.strip()
        clean_sum = re.sub(r"^[!\[\(]?(작업|분석|실행|완료|보고서|오류|TASK)\]?\s*", "", clean_sum, flags=re.IGNORECASE)
        # Collapse multiple spaces
        clean_sum = re.sub(r"\s+", " ", clean_sum).strip()
        if len(clean_sum) > 35:
            clean_sum = clean_sum[:32] + "..."

        return f"[{type_tag}:{repo_short}] {clean_sum} ({date_suffix})"
