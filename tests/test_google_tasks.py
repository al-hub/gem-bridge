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


    @patch("core.google_tasks.build")
    def test_list_pending_tasks_filters_processed_prefixes(self, mock_build):
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        mock_tasks_resource = mock_service.tasks.return_value
        mock_tasks_resource.list.return_value.execute.return_value = {
            "items": [
                {"id": "t1", "title": "새로운 작업", "status": "needsAction"},
                {"id": "t2", "title": "[✅완료] 이전에 끝난 작업", "status": "needsAction"},
                {"id": "t3", "title": "[❌오류] 실패했던 작업", "status": "needsAction"},
                {"id": "t4", "title": "[⏳진행] 처리 중인 작업", "status": "needsAction"},
                {"id": "t5", "title": "[✅완료: f163137] docs/guide.md 수정", "status": "needsAction"},
                {"id": "t6", "title": "[❌오류: 실패] 명령 에러", "status": "needsAction"},
            ]
        }

        manager = GoogleTasksManager(credentials=self.mock_creds)
        pending = manager.list_pending_tasks()

        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["id"], "t1")
        self.assertEqual(pending[0]["title"], "새로운 작업")

    @patch("core.google_tasks.build")
    def test_update_task_with_feedback_success(self, mock_build):
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        mock_tasks_resource = mock_service.tasks.return_value
        mock_tasks_resource.get.return_value.execute.return_value = {
            "id": "t1",
            "title": "기존 작업",
            "notes": "사용자 입력 메모"
        }
        mock_tasks_resource.patch.return_value.execute.return_value = {"id": "t1"}

        manager = GoogleTasksManager(credentials=self.mock_creds)
        success = manager.update_task_with_feedback(
            task_id="t1",
            is_success=True,
            title="기존 작업",
            feedback_notes="[반영 완료] 커밋: abc1234"
        )

        self.assertTrue(success)
        mock_tasks_resource.patch.assert_called_once()
        patch_kwargs = mock_tasks_resource.patch.call_args[1]
        self.assertEqual(patch_kwargs["body"]["title"], "[✅완료] 기존 작업")
        self.assertEqual(patch_kwargs["body"]["status"], "needsAction")
        self.assertIn("사용자 입력 메모", patch_kwargs["body"]["notes"])
        self.assertIn("[반영 완료] 커밋: abc1234", patch_kwargs["body"]["notes"])

    @patch("core.google_tasks.build")
    def test_update_task_with_feedback_error(self, mock_build):
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        mock_tasks_resource = mock_service.tasks.return_value
        mock_tasks_resource.get.return_value.execute.return_value = {
            "id": "t1",
            "title": "[⏳진행] 기존 작업",
            "notes": ""
        }
        mock_tasks_resource.patch.return_value.execute.return_value = {"id": "t1"}

        manager = GoogleTasksManager(credentials=self.mock_creds)
        success = manager.update_task_with_feedback(
            task_id="t1",
            is_success=False,
            title="[⏳진행] 기존 작업",
            feedback_notes="[실행 오류] 저장소 접근 실패"
        )

        self.assertTrue(success)
        mock_tasks_resource.patch.assert_called_once()
        patch_kwargs = mock_tasks_resource.patch.call_args[1]
        self.assertEqual(patch_kwargs["body"]["title"], "[❌오류] 기존 작업")
        self.assertEqual(patch_kwargs["body"]["status"], "needsAction")
        self.assertIn("[실행 오류] 저장소 접근 실패", patch_kwargs["body"]["notes"])

    @patch("core.google_tasks.build")
    def test_update_task_with_feedback_zero_click_title(self, mock_build):
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        mock_tasks_resource = mock_service.tasks.return_value
        mock_tasks_resource.get.return_value.execute.return_value = {
            "id": "t1",
            "title": "gem-bridge docs/guide.md 수정",
            "notes": ""
        }
        mock_tasks_resource.patch.return_value.execute.return_value = {"id": "t1"}

        manager = GoogleTasksManager(credentials=self.mock_creds)
        rich_title = "[✅완료: f163137] docs/guide.md - 에러 핸들링 보강"
        success = manager.update_task_with_feedback(
            task_id="t1",
            is_success=True,
            title=rich_title,
            feedback_notes="[반영 완료] 커밋: f163137"
        )

        self.assertTrue(success)
        patch_kwargs = mock_tasks_resource.patch.call_args[1]
        self.assertEqual(patch_kwargs["body"]["title"], rich_title)

    @patch("core.google_tasks.build")
    def test_archive_stale_tasks(self, mock_build):
        import time
        from datetime import datetime, timezone, timedelta

        mock_service = MagicMock()
        mock_build.return_value = mock_service

        stale_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        fresh_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

        mock_tasks_resource = mock_service.tasks.return_value
        mock_tasks_resource.list.return_value.execute.return_value = {
            "items": [
                {"id": "t_stale", "title": "[✅완료] 어제 작업", "updated": stale_time, "status": "needsAction"},
                {"id": "t_fresh", "title": "[✅완료] 방금 작업", "updated": fresh_time, "status": "needsAction"},
                {"id": "t_active", "title": "진행 대기 작업", "updated": stale_time, "status": "needsAction"},
            ]
        }
        mock_tasks_resource.patch.return_value.execute.return_value = {"id": "t_stale"}

        manager = GoogleTasksManager(credentials=self.mock_creds)
        archived_count = manager.archive_stale_tasks(max_age_hours=24)

        self.assertEqual(archived_count, 1)
        mock_tasks_resource.patch.assert_called_once()
        patch_kwargs = mock_tasks_resource.patch.call_args[1]
        self.assertEqual(patch_kwargs["task"], "t_stale")
        self.assertEqual(patch_kwargs["body"]["status"], "completed")

    def test_task_watermark_create_and_extract(self):
        from core.google_tasks import TaskWatermark
        wm = TaskWatermark.generate(repo="kum", trace_id="trace_123")
        self.assertIn("<!-- GEM_BRIDGE:v=2:channel=tasks:repo=kum:trace=trace_123 -->", wm)
        parsed = TaskWatermark.extract(f"Some notes\n{wm}\nTail notes")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["repo"], "kum")
        self.assertEqual(parsed["trace"], "trace_123")
        self.assertEqual(parsed["version"], "2")

    def test_safe_markdown_truncator_balances_code_blocks(self):
        from core.google_tasks import SafeMarkdownTruncator
        unclosed = "Header\n```python\nline 1\nline 2\nline 3"
        truncated_unclosed = SafeMarkdownTruncator.truncate_body(unclosed, max_length=25)
        self.assertIn("```", truncated_unclosed)

    @patch("core.google_tasks.build")
    def test_retire_previous_tasks_qualifies_only_matching_watermark(self, mock_build):
        from core.google_tasks import TaskWatermark
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_tasks_resource = mock_service.tasks.return_value

        wm_kum = TaskWatermark.generate(repo="kum", trace_id="t_old_trace")
        wm_gem = TaskWatermark.generate(repo="gem-bridge", trace_id="t_gem_trace")

        mock_tasks_resource.list.return_value.execute.return_value = {
            "items": [
                # Matching old task for kum -> should retire
                {"id": "t_kum_old", "title": "[✅완료: 분석] kum - 진목", "notes": f"내용\n{wm_kum}", "status": "needsAction"},
                # Current task -> should skip
                {"id": "t_kum_current", "title": "[✅완료: 분석] kum - 최신", "notes": f"내용\n{wm_kum}", "status": "needsAction"},
                # Other repo task -> should skip
                {"id": "t_gem", "title": "[✅완료] gem-bridge", "notes": f"내용\n{wm_gem}", "status": "needsAction"},
                # Personal user task -> should NEVER touch
                {"id": "t_user", "title": "치과 예약 15시", "notes": "강남역 치과", "status": "needsAction"},
            ]
        }
        mock_tasks_resource.patch.return_value.execute.return_value = {"id": "t_kum_old"}

        manager = GoogleTasksManager(credentials=self.mock_creds)
        retired = manager.retire_previous_tasks(target_repo="kum", current_task_id="t_kum_current")

        self.assertEqual(retired, 1)
        mock_tasks_resource.patch.assert_called_once()
        patch_kwargs = mock_tasks_resource.patch.call_args[1]
        self.assertEqual(patch_kwargs["task"], "t_kum_old")
        self.assertEqual(patch_kwargs["body"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
