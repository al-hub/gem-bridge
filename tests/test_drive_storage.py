"""
Unit tests for core/drive_storage.py
Tests DriveStorageManager, folder caching, and mobile-first title formatting.
"""

import unittest
from unittest.mock import MagicMock
from core.drive_storage import DriveStorageManager
from core.intent_analyzer import TaskType


class TestDriveStorageManager(unittest.TestCase):

    def setUp(self):
        self.mock_drive = MagicMock()
        self.root_folder_id = "root_gemini_bridge_123"
        self.manager = DriveStorageManager(self.mock_drive, self.root_folder_id)

    def test_format_mobile_title_read(self):
        fixed_time = 1789200000.0  # predictable timestamp
        title = self.manager.format_mobile_title(
            task_type=TaskType.READ,
            target_repo="gem-bridge",
            summary="상세 개발이력 분석",
            timestamp=fixed_time
        )
        self.assertTrue(title.startswith("[📄분석:gem-bridge] 상세 개발이력 분석"))
        self.assertTrue(title.endswith(")"))

    def test_format_mobile_title_write(self):
        title = self.manager.format_mobile_title(
            task_type=TaskType.WRITE,
            target_repo="al-hub/gem-bridge.git",
            summary="docs/index.html 타이틀 태그 수정",
            timestamp=1789200000.0
        )
        self.assertTrue(title.startswith("[✅커밋:gem-bridge] docs/index.html 타이틀 태그 수정"))

    def test_format_mobile_title_exec(self):
        title = self.manager.format_mobile_title(
            task_type=TaskType.EXEC,
            target_repo="gem-bridge",
            summary="pytest tests/",
            timestamp=1789200000.0
        )
        self.assertTrue(title.startswith("[💻실행:gem-bridge] pytest tests/"))

    def test_format_mobile_title_error(self):
        title = self.manager.format_mobile_title(
            task_type=TaskType.READ,
            target_repo="gem-bridge",
            summary="GitPushError 발생",
            is_error=True,
            timestamp=1789200000.0
        )
        self.assertTrue(title.startswith("[⚠️오류:gem-bridge] GitPushError 발생"))

    def test_format_mobile_title_truncation(self):
        long_summary = "A" * 100
        title = self.manager.format_mobile_title(
            task_type=TaskType.READ,
            target_repo="gem-bridge",
            summary=long_summary,
            timestamp=1789200000.0
        )
        self.assertTrue("..." in title)
        # Should ensure title is reasonably bounded
        self.assertLess(len(title), 80)

    def test_get_destination_folder_cached(self):
        # Seed cache
        self.manager._folder_cache["reports/2026-09"] = "cached_rep_folder_456"
        res = self.manager.get_destination_folder("reports", auto_monthly=True)
        # Verify it returns cached without API calls
        self.assertEqual(res, "cached_rep_folder_456")
        self.mock_drive.files.assert_not_called()

    def test_find_or_create_subfolder_existing(self):
        # Mock folder list returning an existing folder
        mock_list = MagicMock()
        mock_list.execute.return_value = {"files": [{"id": "sub_folder_789", "name": "reports"}]}
        self.mock_drive.files().list.return_value = mock_list

        fid = self.manager._find_or_create_subfolder(self.root_folder_id, "reports")
        self.assertEqual(fid, "sub_folder_789")
        self.mock_drive.files().create.assert_not_called()

    def test_find_or_create_subfolder_new(self):
        # Mock folder list returning empty, so create is called
        mock_list = MagicMock()
        mock_list.execute.return_value = {"files": []}
        self.mock_drive.files().list.return_value = mock_list

        mock_create = MagicMock()
        mock_create.execute.return_value = {"id": "new_folder_999", "name": "reports"}
        self.mock_drive.files().create.return_value = mock_create

        fid = self.manager._find_or_create_subfolder(self.root_folder_id, "reports")
        self.assertEqual(fid, "new_folder_999")
        self.mock_drive.files().create.assert_called_once()


if __name__ == "__main__":
    unittest.main()
