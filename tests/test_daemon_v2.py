import unittest
from unittest.mock import MagicMock, patch
from daemon_v2 import GemBridgeDaemonV2
from core.intent_analyzer import IntentAnalysisResult, TaskType


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

    def test_filter_candidate_documents(self):
        self.daemon.drive_service.files().list().execute.return_value = {
            "files": [
                {"id": "doc1", "name": "!분석 gem-bridge"},
                {"id": "doc2", "name": "[보고서] 완료된 분석"},  # Should ignore
                {"id": "doc3", "name": "[오류] 실패한 작업"},     # Should ignore
                {"id": "doc4", "name": "일상 메모"},             # No trigger keyword
                {"id": "doc5", "name": "깃 리포지토리 task"},
            ]
        }
        candidates = self.daemon.find_candidate_documents()
        cand_ids = [c["id"] for c in candidates]
        self.assertIn("doc1", cand_ids)
        self.assertIn("doc5", cand_ids)
        self.assertNotIn("doc2", cand_ids)
        self.assertNotIn("doc3", cand_ids)
        self.assertNotIn("doc4", cand_ids)

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
        found_error_doc = any("[오류]" in call[1]["body"]["name"] for call in create_calls)
        self.assertTrue(found_error_doc)


if __name__ == "__main__":
    unittest.main()
