import unittest
from unittest.mock import MagicMock, patch
from core.google_tasks import GoogleTasksManager, TASKS_SCOPE, DRIVE_SCOPE, COMBINED_SCOPES


class TestGoogleTasksManager(unittest.TestCase):

    def setUp(self):
        self.mock_creds = MagicMock()
        self.mock_creds.scopes = [DRIVE_SCOPE, TASKS_SCOPE]

    @patch("core.google_tasks.build")
    def test_init_service_success(self, mock_build):
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        manager = GoogleTasksManager(credentials=self.mock_creds)
        self.assertTrue(manager.is_available)
        mock_build.assert_called_once_with("tasks", "v1", credentials=self.mock_creds, cache_discovery=False)

    def test_init_service_missing_scope(self):
        creds_no_scope = MagicMock()
        creds_no_scope.scopes = [DRIVE_SCOPE]

        manager = GoogleTasksManager(credentials=creds_no_scope)
        self.assertFalse(manager.is_available)

    def test_init_service_no_creds(self):
        manager = GoogleTasksManager(credentials=None)
        self.assertFalse(manager.is_available)

    @patch("core.google_tasks.build")
    def test_list_pending_tasks(self, mock_build):
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        mock_tasks_resource = mock_service.tasks.return_value
        mock_tasks_resource.list.return_value.execute.return_value = {
            "items": [
                {"id": "t1", "title": "gem-bridge docs/guide.md 수정", "status": "needsAction"},
                {"id": "t2", "title": "이미 완료된 작업", "status": "completed"},
                {"id": "t3", "title": "   ", "status": "needsAction"},  # blank title
            ]
        }

        manager = GoogleTasksManager(credentials=self.mock_creds)
        pending = manager.list_pending_tasks()

        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["id"], "t1")
        self.assertEqual(pending[0]["title"], "gem-bridge docs/guide.md 수정")

    @patch("core.google_tasks.build")
    def test_complete_task(self, mock_build):
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        mock_tasks_resource = mock_service.tasks.return_value
        mock_tasks_resource.get.return_value.execute.return_value = {
            "id": "t1",
            "title": "gem-bridge docs/guide.md 수정",
            "notes": "기존 메모"
        }
        mock_tasks_resource.patch.return_value.execute.return_value = {
            "id": "t1",
            "status": "completed"
        }

        manager = GoogleTasksManager(credentials=self.mock_creds)
        success = manager.complete_task("t1", completion_notes="✅ 커밋 완료: abc1234")

        self.assertTrue(success)
        mock_tasks_resource.patch.assert_called_once()
        patch_call_kwargs = mock_tasks_resource.patch.call_args[1]
        self.assertEqual(patch_call_kwargs["task"], "t1")
        self.assertEqual(patch_call_kwargs["body"]["status"], "completed")
        self.assertIn("기존 메모", patch_call_kwargs["body"]["notes"])
        self.assertIn("✅ 커밋 완료: abc1234", patch_call_kwargs["body"]["notes"])


if __name__ == "__main__":
    unittest.main()
