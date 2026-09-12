"""
core/janitor.py - Lifecycle and Retention Management Engine for gem-bridge
Automates tiered document retention, trash soft-deletion, and hard-purging
to keep Google Drive tidy and prevent Gemini AI citation confusion.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional
from core.drive_storage import DriveStorageManager

logger = logging.getLogger("gem_bridge.janitor")


class StorageJanitor:
    """
    Manages document lifecycle, retention rules, and trash purging for GeminiBridge.
    """

    DEFAULT_RETENTION_DAYS = {
        "logs": 3,      # CLI execution outputs, stderr logs, error reports
        "commits": 7,   # Git commit diff completion confirmations
        "reports": 30,  # Deep Gemini analysis reports
    }

    def __init__(
        self,
        drive_service,
        root_folder_id: str,
        storage_manager: Optional[DriveStorageManager] = None,
        retention_days: Optional[Dict[str, int]] = None
    ):
        self.drive_service = drive_service
        self.root_folder_id = root_folder_id
        self.storage_manager = storage_manager
        self.retention_days = retention_days or dict(self.DEFAULT_RETENTION_DAYS)

    def clean_expired_documents(self) -> Dict[str, int]:
        """
        Scans categories (logs, commits, reports) and moves documents
        exceeding their retention policy to the Google Drive Trash.
        Returns a dict of {category: count_trashed}.
        """
        if not self.drive_service or not self.root_folder_id:
            return {}

        results = {}
        now = time.time()

        for category, days in self.retention_days.items():
            cutoff_epoch = now - (days * 86400)
            trashed_count = self._clean_category(category, cutoff_epoch)
            results[category] = trashed_count

        logger.info(f"[Janitor] Expired documents cleanup complete: {results}")
        return results

    def _clean_category(self, category: str, cutoff_epoch: float) -> int:
        """Finds subfolders or files for a category and soft-deletes expired ones."""
        trashed_count = 0
        try:
            # Find category parent folder
            q_cat = (
                f"mimeType = 'application/vnd.google-apps.folder' "
                f"and name = '{category}' "
                f"and '{self.root_folder_id}' in parents "
                f"and trashed = false"
            )
            cat_res = self.drive_service.files().list(q=q_cat, fields="files(id, name)").execute()
            cat_folders = cat_res.get("files", [])
            if not cat_folders:
                return 0

            cat_id = cat_folders[0]["id"]

            # Query all files recursively or in subfolders of this category
            # 1. Direct children in category folder
            # 2. Children in monthly subfolders (e.g. 2026-09/)
            folders_to_scan = [cat_id]
            q_sub = (
                f"mimeType = 'application/vnd.google-apps.folder' "
                f"and '{cat_id}' in parents "
                f"and trashed = false"
            )
            sub_res = self.drive_service.files().list(q=q_sub, fields="files(id, name)").execute()
            for sub in sub_res.get("files", []):
                folders_to_scan.append(sub["id"])

            for fid in folders_to_scan:
                q_files = (
                    f"'{fid}' in parents "
                    f"and mimeType != 'application/vnd.google-apps.folder' "
                    f"and trashed = false"
                )
                file_res = self.drive_service.files().list(
                    q=q_files,
                    fields="files(id, name, createdTime, modifiedTime)"
                ).execute()

                for file_item in file_res.get("files", []):
                    # Never trash CONSOLE or STATUS if accidentally placed
                    if file_item.get("name") in ("CONSOLE", "STATUS"):
                        continue

                    # Check modification or creation time
                    mod_time_str = file_item.get("modifiedTime") or file_item.get("createdTime")
                    if mod_time_str:
                        file_epoch = self._parse_iso_to_epoch(mod_time_str)
                        if file_epoch and file_epoch < cutoff_epoch:
                            self.drive_service.files().update(
                                fileId=file_item["id"],
                                body={"trashed": True}
                            ).execute()
                            trashed_count += 1
                            logger.info(f"[Janitor] Trashed expired file: {file_item.get('name')} (ID: {file_item['id']})")
        except Exception as e:
            logger.warning(f"[Janitor] Error cleaning category '{category}': {e}")

        return trashed_count

    def purge_trash(self, max_files: int = 50) -> int:
        """
        Permanently deletes files from Google Drive trash to eliminate
        AI citation noise and save storage space.
        Returns count of permanently deleted files.
        """
        if not self.drive_service:
            return 0

        purged_count = 0
        try:
            # Query trashed documents
            q = "trashed = true"
            res = self.drive_service.files().list(
                q=q,
                fields="files(id, name)",
                pageSize=max_files
            ).execute()

            for item in res.get("files", []):
                try:
                    self.drive_service.files().delete(fileId=item["id"]).execute()
                    purged_count += 1
                    logger.info(f"[Janitor] Hard purged trashed document: {item.get('name')} (ID: {item['id']})")
                except Exception as del_err:
                    logger.warning(f"[Janitor] Could not purge file {item.get('id')}: {del_err}")
        except Exception as e:
            logger.warning(f"[Janitor] Failed to query trash for purge: {e}")

        return purged_count

    def get_storage_stats(self) -> Dict[str, int]:
        """Returns document counts across GeminiBridge subfolders and trash."""
        stats = {"reports": 0, "commits": 0, "logs": 0, "archive": 0, "root": 0, "trash": 0}
        if not self.drive_service or not self.root_folder_id:
            return stats

        try:
            # Root items
            q_root = f"'{self.root_folder_id}' in parents and trashed = false"
            res_root = self.drive_service.files().list(q=q_root, fields="files(id, name, mimeType)").execute()
            for item in res_root.get("files", []):
                if item.get("mimeType") == "application/vnd.google-apps.folder":
                    folder_name = item.get("name", "")
                    if folder_name in stats:
                        # Count items inside this folder
                        q_sub = f"'{item['id']}' in parents and trashed = false"
                        sub_res = self.drive_service.files().list(q=q_sub, fields="files(id)").execute()
                        stats[folder_name] = len(sub_res.get("files", []))
                else:
                    stats["root"] += 1

            # Trash items count
            q_trash = "trashed = true"
            trash_res = self.drive_service.files().list(q=q_trash, fields="files(id)", pageSize=100).execute()
            stats["trash"] = len(trash_res.get("files", []))

        except Exception as e:
            logger.warning(f"[Janitor] Error fetching storage stats: {e}")

        return stats

    @staticmethod
    def _parse_iso_to_epoch(iso_str: str) -> Optional[float]:
        """Parses ISO-8601 timestamp string into epoch seconds."""
        try:
            clean_str = iso_str.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_str)
            return dt.timestamp()
        except Exception:
            return None
