import unittest
from unittest.mock import MagicMock, patch
from core.intent_analyzer import IntentAnalyzer, IntentAnalysisResult, TaskType


class TestIntentAnalyzer(unittest.TestCase):

    def setUp(self):
        self.analyzer = IntentAnalyzer(api_key="fake_api_key", default_repo="gem-bridge")

    def test_detect_command_prefix(self):
        self.assertEqual(
            self.analyzer._detect_command_prefix("!분석 gem-bridge 구조", ""),
            TaskType.READ
        )
        self.assertEqual(
            self.analyzer._detect_command_prefix("!analyze architecture", ""),
            TaskType.READ
        )
        self.assertEqual(
            self.analyzer._detect_command_prefix("!작업 add new feature", ""),
            TaskType.WRITE
        )
        self.assertEqual(
            self.analyzer._detect_command_prefix("!실행 pytest tests/", ""),
            TaskType.EXEC
        )
        self.assertIsNone(
            self.analyzer._detect_command_prefix("일반 문서 제목", "일반 본문 내용")
        )

    def test_direct_json_write_payload(self):
        raw_json = '''{
            "repo": "gem-bridge",
            "target_path": "docs/NEW_DOC.md",
            "content": "# New Documentation\\nContent here",
            "commit_message": "docs: add NEW_DOC.md"
        }'''
        result = self.analyzer.analyze(raw_json)
        self.assertEqual(result.task_type, TaskType.WRITE)
        self.assertEqual(result.target_repo, "gem-bridge")
        self.assertEqual(result.target_path, "docs/NEW_DOC.md")
        self.assertIn("New Documentation", result.content)

    def test_direct_json_read_query(self):
        raw_json = '{"repo": "gem-bridge", "query": "How does repo_manager work?"}'
        result = self.analyzer.analyze(raw_json)
        self.assertEqual(result.task_type, TaskType.READ)
        self.assertEqual(result.target_repo, "gem-bridge")
        self.assertEqual(result.query, "How does repo_manager work?")

    def test_default_read_enforcement_on_missing_write_details(self):
        # Even if model returns WRITE, if target_path or content is missing, downgrade to READ
        mock_response = MagicMock()
        mock_response.text = '{"task_type": "WRITE", "target_repo": "gem-bridge", "summary": "update file", "target_path": null, "content": null}'
        
        with patch.object(self.analyzer.client.models, "generate_content", return_value=mock_response):
            result = self.analyzer._analyze_with_llm("코드를 조금 고쳐줘", ["gem-bridge"])
            self.assertEqual(result.task_type, TaskType.READ)
            self.assertIn("Downgraded to READ", result.reasoning)

    def test_fallback_read_on_llm_exception(self):
        with patch.object(self.analyzer.client.models, "generate_content", side_effect=Exception("API Timeout")):
            result = self.analyzer.analyze("테스트 본문", title="테스트 제목")
            self.assertEqual(result.task_type, TaskType.READ)
            self.assertEqual(result.target_repo, "gem-bridge")
            self.assertIn("API Timeout", result.reasoning)


if __name__ == "__main__":
    unittest.main()
