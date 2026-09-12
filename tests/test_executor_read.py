import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from core.intent_analyzer import IntentAnalysisResult, TaskType
from core.executor_read import ReadExecutor


class TestExecutorRead(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_dir.name)
        (self.repo_dir / "README.md").write_text("# Test Repo\nSample documentation.", encoding="utf-8")
        src_dir = self.repo_dir / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "app.py").write_text("print('hello world')", encoding="utf-8")

        self.mock_drive = MagicMock()
        self.mock_drive.files().create().execute.return_value = {
            "id": "doc_read_123",
            "name": "[보고서] 아키텍처 분석",
            "webViewLink": "https://docs.google.com/doc_read_123"
        }

        self.mock_gemini = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "# [보고서] 분석 결과\n## 1. 개요\n분석 내용 요약."
        self.mock_gemini.models.generate_content.return_value = mock_response

        self.executor = ReadExecutor(
            drive_service=self.mock_drive,
            gemini_client=self.mock_gemini
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_gather_context_and_execute_read(self):
        intent = IntentAnalysisResult(
            task_type=TaskType.READ,
            target_repo="test-repo",
            summary="Analyze repository structure",
            query="전체 구조를 분석해줘"
        )
        result = self.executor.execute(self.repo_dir, intent, original_title="!분석 gem-bridge 구조")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["task_type"], "READ")
        self.assertEqual(result["doc_id"], "doc_read_123")
        self.assertEqual(result["doc_name"], "[보고서] gem-bridge 구조")

        # Verify Drive API was called with [보고서] title
        create_call = self.mock_drive.files().create
        self.assertTrue(create_call.called)
        call_kwargs = create_call.call_args[1]
        self.assertEqual(call_kwargs["body"]["name"], "[보고서] gem-bridge 구조")
        self.assertEqual(call_kwargs["body"]["mimeType"], "application/vnd.google-apps.document")

        # Verify no files in repo were changed or created
        self.assertFalse((self.repo_dir / "report.md").exists())

    def test_clean_doc_title(self):
        self.assertEqual(self.executor._clean_doc_title("!분석 시스템 구조"), "시스템 구조")
        self.assertEqual(self.executor._clean_doc_title("!read codebase"), "codebase")
        self.assertEqual(self.executor._clean_doc_title("[보고서] 이미 있는 제목"), "이미 있는 제목")

    def test_generate_report_deep_tier_fallback(self):
        intent = IntentAnalysisResult(
            task_type=TaskType.READ,
            target_repo="test-repo",
            summary="심층 아키텍처 분석",
            query="심층 구조 분석해줘",
            model_tier="deep"
        )
        mock_response = MagicMock()
        mock_response.text = "# [보고서] 심층 분석 결과\n상세 내용."

        # 3.8-flash fails, 3.6-flash succeeds
        self.mock_gemini.models.generate_content.side_effect = [
            RuntimeError("503 UNAVAILABLE"),
            mock_response
        ]

        result = self.executor.execute(self.repo_dir, intent, original_title="!분석 test-repo")
        self.assertEqual(result["status"], "success")
        self.assertIn("심층 분석 결과", result["report"])
        self.assertEqual(self.mock_gemini.models.generate_content.call_count, 2)
        first_call = self.mock_gemini.models.generate_content.call_args_list[0][1]
        second_call = self.mock_gemini.models.generate_content.call_args_list[1][1]
        self.assertEqual(first_call["model"], "gemini-3.8-flash")
        self.assertEqual(second_call["model"], "gemini-3.6-flash")


if __name__ == "__main__":
    unittest.main()
