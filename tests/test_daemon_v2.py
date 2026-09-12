import unittest
from unittest.mock import MagicMock, patch
from daemon_v2 import GemBridgeDaemonV2
from core.intent_analyzer import IntentAnalysisResult, TaskType
from core.console_protocol import ConsoleProtocolParser, ConsoleDocFormatter


class TestDaemonV2(unittest.TestCase):

    def setUp(self):
        self.config = {
            "poll_interval_seconds": 1,
            "repositories": {
                "gem-bridge": "/tmp/dummy-gem-bridge"
            },
            "gemini_api_key": "test_key"
        }
        with patch("daemon_v2.get_drive_service") as mock_get_drive:
            self.mock_drive = MagicMock()
            mock_get_drive.return_value = self.mock_drive
            self.daemon = GemBridgeDaemonV2(self.config)
            self.daemon.console_doc_id = "mock_console_doc_id"
            self.daemon.status_doc_id = "mock_status_doc_id"
            self.daemon.drive_service.reset_mock()

    def test_filter_candidate_documents_excludes_system_and_console(self):
        self.daemon.drive_service.files().list().execute.return_value = {
            "files": [
                {"id": "doc1", "name": "!분석 gem-bridge"},
                {"id": "doc2", "name": "[보고서] 완료된 분석"},  # Should ignore
                {"id": "doc3", "name": "[오류] 실패한 작업"},     # Should ignore
                {"id": "doc4", "name": "일상 메모"},             # No trigger keyword
                {"id": "doc5", "name": "깃 리포지토리 task"},
                {"id": "mock_console_doc_id", "name": "CONSOLE"},  # Must ignore CONSOLE
                {"id": "mock_status_doc_id", "name": "STATUS"},    # Must ignore STATUS
            ]
        }
        candidates = self.daemon.find_candidate_documents()
        cand_ids = [c["id"] for c in candidates]
        self.assertIn("doc1", cand_ids)
        self.assertIn("doc5", cand_ids)
        self.assertNotIn("doc2", cand_ids)
        self.assertNotIn("doc3", cand_ids)
        self.assertNotIn("doc4", cand_ids)
        self.assertNotIn("mock_console_doc_id", cand_ids)
        self.assertNotIn("mock_status_doc_id", cand_ids)

    def test_process_read_task_dispatched_and_trashed(self):
        doc_id = "test_doc_read_01"
        doc_name = "!분석 아키텍처"

        # Mock drive export
        self.daemon.drive_service.files().export_media().execute.return_value = "구조 분석 요청".encode("utf-8")

        # Mock intent analyzer
        mock_intent = IntentAnalysisResult(
            task_type=TaskType.READ,
            target_repo="gem-bridge",
            summary="아키텍처 분석",
            query="구조 분석 요청"
        )
        self.daemon.intent_analyzer.analyze = MagicMock(return_value=mock_intent)
        self.daemon.repo_manager.prepare_repo = MagicMock(return_value="/tmp/dummy-gem-bridge")
        self.daemon.read_executor.execute = MagicMock(return_value={"status": "success", "doc_id": "rep_123", "doc_name": "[보고서] 아키텍처"})

        self.daemon.process_single_task(doc_id, doc_name)

        # Verify read executor called
        self.daemon.read_executor.execute.assert_called_once()
        # Verify original doc trashed
        self.daemon.drive_service.files().update.assert_called_with(
            fileId=doc_id, body={"trashed": True}
        )

    def test_crash_prevention_on_error(self):
        doc_id = "test_doc_err_01"
        doc_name = "!작업 잘못된 요청"

        # Mock drive export throwing exception
        self.daemon.drive_service.files().export_media().execute.side_effect = RuntimeError("Drive network failure")

        # Must not raise exception (crash prevention)
        self.daemon.process_single_task(doc_id, doc_name)

        # Verify error doc creation attempted
        create_calls = self.daemon.drive_service.files().create.call_args_list
        found_error_doc = any("오류" in call[1]["body"]["name"] for call in create_calls)
        self.assertTrue(found_error_doc)

    def test_console_anti_echo_prevention(self):
        # 1. Simulate identical modifiedTime -> Should skip immediately
        self.daemon._last_console_modified_time = "2026-09-12T16:00:00Z"
        self.daemon.drive_service.files().get().execute.return_value = {
            "modifiedTime": "2026-09-12T16:00:00Z"
        }
        self.daemon.check_and_process_console()
        # export_media should not be called
        self.daemon.drive_service.files().export_media.assert_not_called()

        # 2. Simulate changed modifiedTime but identical full content hash (Layer 1)
        self.daemon.drive_service.files().get().execute.return_value = {
            "modifiedTime": "2026-09-12T16:01:00Z"
        }
        doc_content = ConsoleDocFormatter.render(status="ONLINE", input_command="!작업 kum")
        self.daemon._last_console_content_hash = ConsoleProtocolParser.compute_content_hash(doc_content)
        self.daemon.drive_service.files().export_media().execute.return_value = doc_content.encode("utf-8")

        self.daemon.intent_analyzer.analyze = MagicMock()
        self.daemon.check_and_process_console()
        # Intent analyzer should NOT be called (Skipped by Layer 1 hash check)
        self.daemon.intent_analyzer.analyze.assert_not_called()

        # 3. Simulate command already executed previously (Layer 2 command hash)
        self.daemon._last_console_content_hash = "different_hash"
        cmd = "!작업 kum 이미완료된명령"
        doc_content_with_old_cmd = ConsoleDocFormatter.render(status="ONLINE", input_command=cmd)
        self.daemon._last_processed_command_hash = ConsoleProtocolParser.compute_command_hash(cmd)
        self.daemon.drive_service.files().export_media().execute.return_value = doc_content_with_old_cmd.encode("utf-8")

        self.daemon.check_and_process_console()
        # Intent analyzer should NOT be called (Skipped by Layer 2 command hash)
        self.daemon.intent_analyzer.analyze.assert_not_called()

    def test_console_execution_write_flow(self):
        cmd = "!작업 kum 랜딩페이지 개선"
        doc_content = ConsoleDocFormatter.render(status="ONLINE", input_command=cmd)

        self.daemon.drive_service.files().get().execute.return_value = {
            "modifiedTime": "2026-09-12T16:05:00Z"
        }
        self.daemon.drive_service.files().export_media().execute.return_value = doc_content.encode("utf-8")
        self.daemon._last_console_modified_time = "old_time"
        self.daemon._last_console_content_hash = "old_content_hash"
        self.daemon._last_processed_command_hash = "old_cmd_hash"

        mock_intent = IntentAnalysisResult(
            task_type=TaskType.WRITE,
            target_repo="kum",
            summary="랜딩페이지 개선",
            target_path="index.html",
            content="<h1>New Title</h1>",
            commit_message="feat: improve landing page"
        )
        self.daemon.intent_analyzer.analyze = MagicMock(return_value=mock_intent)
        self.daemon.repo_manager.prepare_repo = MagicMock(return_value="/tmp/dummy-kum")
        self.daemon.write_executor.execute = MagicMock(return_value={
            "status": "success",
            "commit_hash": "abcdef1234567890",
            "commit_message": "feat: improve landing page",
            "target_path": "index.html",
            "diff": "+ <h1>New Title</h1>"
        })

        self.daemon.check_and_process_console()

        # Check that write executor was called
        self.daemon.write_executor.execute.assert_called_once()
        # Check that command hash was updated
        self.assertEqual(
            self.daemon._last_processed_command_hash,
            ConsoleProtocolParser.compute_command_hash(cmd)
        )

    def test_console_telemetry_trace_and_duration(self):
        cmd = "!작업 kum 랜딩페이지 개선"
        doc_content = ConsoleDocFormatter.render(status="ONLINE", input_command=cmd)
        self.daemon.drive_service.files().get().execute.return_value = {
            "modifiedTime": "2026-09-12T07:19:20.000Z"
        }
        self.daemon.drive_service.files().export_media().execute.return_value = doc_content.encode("utf-8")
        self.daemon._last_console_modified_time = "old_time"
        self.daemon._last_console_content_hash = "old_content_hash"
        self.daemon._last_processed_command_hash = "old_cmd_hash"

        mock_intent = IntentAnalysisResult(
            task_type=TaskType.WRITE,
            target_repo="kum",
            summary="랜딩페이지 개선",
            target_path="index.html",
            content="<h1>New Title</h1>",
            commit_message="feat: improve landing page"
        )
        self.daemon.intent_analyzer.analyze = MagicMock(return_value=mock_intent)
        self.daemon.repo_manager.prepare_repo = MagicMock(return_value="/tmp/dummy-kum")
        self.daemon.write_executor.execute = MagicMock(return_value={
            "status": "success",
            "commit_hash": "abcdef1234567890",
            "commit_message": "feat: improve landing page",
            "target_path": "index.html",
            "diff": "+ <h1>New Title</h1>"
        })

        self.daemon.check_and_process_console()

        update_calls = self.daemon.drive_service.files().update.call_args_list
        console_updates = [c for c in update_calls if c[1].get("fileId") == "mock_console_doc_id"]
        self.assertTrue(len(console_updates) >= 2)
        media = console_updates[-1][1].get("media_body")
        last_body = media.getbytes(0, media.size()).decode("utf-8")
        self.assertIn("Trace ID", last_body)
        self.assertIn("소요 시간", last_body)
        self.assertIn("동기화 지연", last_body)

    def test_console_offline_shutdown(self):
        doc_content = ConsoleDocFormatter.render(status="ONLINE", input_command="!작업 kum")
        self.daemon.drive_service.files().export_media().execute.return_value = doc_content.encode("utf-8")

        self.daemon._set_console_offline()

    def test_adaptive_polling_sleep_interval(self):
        import time
        # Active mode (< 300s since activity)
        self.daemon._last_activity_time = time.time()
        self.assertEqual(self.daemon.get_sleep_interval(), 1.0)

        # Idle mode (> 300s since activity)
        self.daemon._last_activity_time = time.time() - 350
        self.assertEqual(self.daemon.get_sleep_interval(), 1.0 if self.daemon.poll_interval == 1 else self.daemon.poll_interval)

    def test_non_destructive_overwrite_guard(self):
        # 1. Setup initial command
        initial_doc = ConsoleDocFormatter.render(status="ONLINE", input_command="!작업 kum 랜딩페이지")
        self.daemon.drive_service.files().get().execute.return_value = {
            "id": "mock_console_doc_id", "modifiedTime": "2026-09-12T18:00:00Z"
        }
        self.daemon._last_console_modified_time = "old_time"
        self.daemon._last_console_content_hash = "old_hash"
        self.daemon._last_processed_command_hash = "old_cmd_hash"

        # Mock export_media: first returns initial command, then during task execution returns NEW command typed by user
        new_command_during_task = "!작업 kum 추가 수정건"
        user_edited_doc = ConsoleDocFormatter.render(status="PROCESSING", input_command=new_command_during_task)

        self.daemon.drive_service.files().export_media().execute.side_effect = [
            initial_doc.encode("utf-8"),       # Step 2: Read command
            user_edited_doc.encode("utf-8"),   # Step 10: Check CONSOLE before final render
        ]

        mock_intent = IntentAnalysisResult(
            task_type=TaskType.WRITE,
            target_repo="kum",
            summary="랜딩페이지",
            target_path="index.html",
            content="<h1>New</h1>",
            commit_message="feat: new"
        )
        self.daemon.intent_analyzer.analyze = MagicMock(return_value=mock_intent)
        self.daemon.repo_manager.prepare_repo = MagicMock(return_value="/tmp/dummy-kum")
        self.daemon.write_executor.execute = MagicMock(return_value={
            "status": "success", "commit_hash": "abc1234", "commit_message": "feat: new",
            "target_path": "index.html", "diff": "+ <h1>New</h1>"
        })

        self.daemon.check_and_process_console()

        # Verify that final write retained the new user command
        update_calls = self.daemon.drive_service.files().update.call_args_list
        console_updates = [c for c in update_calls if c[1].get("fileId") == "mock_console_doc_id"]
        self.assertTrue(len(console_updates) >= 2)
        media = console_updates[-1][1].get("media_body")
        final_body = media.getbytes(0, media.size()).decode("utf-8")
        self.assertIn(new_command_during_task, final_body)

    def test_fast_path_execution_bypasses_llm(self):
        doc_content = ConsoleDocFormatter.render(status="ONLINE", input_command="!실행 pytest tests/")
        self.daemon.drive_service.files().get().execute.return_value = {
            "id": "mock_console_doc_id", "modifiedTime": "2026-09-12T18:05:00Z"
        }
        self.daemon.drive_service.files().export_media().execute.return_value = doc_content.encode("utf-8")
        self.daemon._last_console_modified_time = "old_time"
        self.daemon._last_console_content_hash = "old_hash"
        self.daemon._last_processed_command_hash = "old_cmd_hash"

        self.daemon.intent_analyzer.analyze = MagicMock()
        self.daemon.repo_manager.prepare_repo = MagicMock(return_value="/tmp/dummy-repo")
        self.daemon.exec_executor.execute = MagicMock(return_value={"stdout": "57 passed", "stderr": "", "exit_code": 0})

        self.daemon.check_and_process_console()

        # intent_analyzer.analyze should NOT be called due to Fast-Path!
        self.daemon.intent_analyzer.analyze.assert_not_called()
        self.daemon.exec_executor.execute.assert_called_once()

    def test_top_anchored_layout_above_the_fold(self):
        rendered = ConsoleDocFormatter.render(
            status="ONLINE",
            input_command="!작업 test",
            output_content="최신 결과 출력"
        )
    def test_check_and_process_google_tasks_write(self):
        # Mock GoogleTasksManager
        mock_tasks_mgr = MagicMock()
        mock_tasks_mgr.is_available = True
        mock_tasks_mgr.list_pending_tasks.return_value = [
            {"id": "gtask_123", "title": "gem-bridge docs/guide.md 수정하고 푸시해줘", "notes": ""}
        ]
        self.daemon.tasks_manager = mock_tasks_mgr

        # Mock intent analyzer to return WRITE
        self.daemon.intent_analyzer.analyze = MagicMock(return_value=IntentAnalysisResult(
            task_type=TaskType.WRITE,
            target_repo="gem-bridge",
            summary="docs/guide.md 수정",
            target_path="docs/guide.md",
            instruction="모바일 사용법 추가",
            commit_message="docs: update guide.md via 0-tap"
        ))
        self.daemon.repo_manager.prepare_repo = MagicMock(return_value="/tmp/dummy-gem-bridge")
        self.daemon.write_executor.execute = MagicMock(return_value={
            "status": "success", "commit_hash": "a1b2c3d", "commit_message": "docs: update guide.md via 0-tap",
            "target_path": "docs/guide.md", "diff": "+ new content"
        })
        self.daemon._create_completion_doc = MagicMock()
        self.daemon._sync_task_result_to_console = MagicMock()

        self.daemon.check_and_process_google_tasks()

        self.assertIn("gtask_123", self.daemon.processed_task_ids)
        self.daemon.write_executor.execute.assert_called_once()
        mock_tasks_mgr.complete_task.assert_called_once_with(
            task_id="gtask_123",
            completion_notes="✅ [gem-bridge 완료] 커밋: a1b2c3d - docs: update guide.md via 0-tap"
        )
        self.daemon._sync_task_result_to_console.assert_called_once()


if __name__ == "__main__":
    unittest.main()
