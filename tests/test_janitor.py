"""
Unit tests for core/janitor.py
Tests StorageJanitor, retention checks, trash purging, and storage statistics.
"""

import unittest
from unittest.mock import MagicMock
from core.janitor import StorageJanitor


class TestStorageJanitor(unittest.TestCase):

    def setUp(self):
        self.mock_drive = MagicMock()
        self.root_folder_id = "root_gemini_bridge_123"
        self.janitor = StorageJanitor(
            drive_service=self.mock_drive,
            root_folder_id=self.root_folder_id,
            retention_days={"logs": 3, "commits": 7}
        )

    def test_parse_iso_to_epoch(self):
        epoch = self.janitor._parse_iso_to_epoch("2026-09-12T17:00:00Z")
        self.assertIsNotNone(epoch)
        self.assertGreater(epoch, 0)

        # Invalid string
        self.assertIsNone(self.janitor._parse_iso_to_epoch("invalid-date"))

    def test_clean_category_trashes_expired(self):
        # 1. Category folder list returns one folder
        mock_cat_list = MagicMock()
        mock_cat_list.execute.return_value = {"files": [{"id": "cat_folder_logs", "name": "logs"}]}

        # 2. Subfolders query returns empty
        mock_sub_list = MagicMock()
        mock_sub_list.execute.return_value = {"files": []}

        # 3. Files query returns 1 expired file and 1 fresh file
        mock_files_list = MagicMock()
        mock_files_list.execute.return_value = {
            "files": [
                {
                    "id": "file_expired",
                    "name": "old_log.txt",
                    "modifiedTime": "2020-01-01T00:00:00Z"
                },
                {
                    "id": "file_fresh",
                    "name": "recent_log.txt",
                    "modifiedTime": "2030-01-01T00:00:00Z"
                }
            ]
        }

        self.mock_drive.files().list.side_effect = [
            mock_cat_list,
            mock_sub_list,
            mock_files_list
        ]

        trashed = self.janitor._clean_category("logs", cutoff_epoch=1780000000.0)
        self.assertEqual(trashed, 1)
        self.mock_drive.files().update.assert_called_once_with(
            fileId="file_expired",
            body={"trashed": True}
        )

    def test_purge_trash(self):
        # Mock trash list returning 2 files
        mock_list = MagicMock()
        mock_list.execute.return_value = {
            "files": [
                {"id": "trash_01", "name": "err1.gdoc"},
                {"id": "trash_02", "name": "err2.gdoc"}
            ]
        }
        self.mock_drive.files().list.return_value = mock_list

        purged = self.janitor.purge_trash(max_files=10)
        self.assertEqual(purged, 2)
        self.assertEqual(self.mock_drive.files().delete.call_count, 2)

    def test_get_storage_stats(self):
        # Mock root listing
        mock_root = MagicMock()
        mock_root.execute.return_value = {
            "files": [
                {"id": "f_rep", "name": "reports", "mimeType": "application/vnd.google-apps.folder"},
                {"id": "f_console", "name": "CONSOLE", "mimeType": "application/vnd.google-apps.document"}
            ]
        }
        # Mock reports subfolder listing
        mock_rep_files = MagicMock()
        mock_rep_files.execute.return_value = {"files": [{"id": "r1"}, {"id": "r2"}]}

        # Mock trash listing
        mock_trash = MagicMock()
        mock_trash.execute.return_value = {"files": [{"id": "t1"}]}

        self.mock_drive.files().list.side_effect = [
            mock_root,
            mock_rep_files,
            mock_trash
        ]

        stats = self.janitor.get_storage_stats()
        self.assertEqual(stats["reports"], 2)
        self.assertEqual(stats["root"], 1)  # CONSOLE
        self.assertEqual(stats["trash"], 1)


if __name__ == "__main__":
    unittest.main()
