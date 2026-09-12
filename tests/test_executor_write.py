import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from core.intent_analyzer import IntentAnalysisResult, TaskType
from core.executor_write import WriteExecutor, ProtectedFileError


class TestExecutorWrite(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_dir.name)
        # Create a protected file
        self.readme_path = self.repo_dir / "README.md"
        self.readme_path.write_text("# Original README\nDo not overwrite!", encoding="utf-8")
        
        # Create a normal file
        self.code_path = self.repo_dir / "src" / "feature.py"
        self.code_path.parent.mkdir(parents=True, exist_ok=True)
        self.code_path.write_text("def old_function(): pass\n", encoding="utf-8")

        self.executor = WriteExecutor()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_protected_file_detection(self):
        self.assertTrue(self.executor.is_protected("README.md"))
        self.assertTrue(self.executor.is_protected("ARCHITECTURE.md"))
        self.assertTrue(self.executor.is_protected("docs/ARCHITECTURE.md"))
        self.assertTrue(self.executor.is_protected("credentials.json"))
        self.assertTrue(self.executor.is_protected("token.json"))
        self.assertTrue(self.executor.is_protected(".env"))
        self.assertFalse(self.executor.is_protected("src/main.py"))
        self.assertFalse(self.executor.is_protected("docs/guide.md"))

    def test_protected_file_overwrite_blocked(self):
        intent = IntentAnalysisResult(
            task_type=TaskType.WRITE,
            target_repo="gem-bridge",
            summary="Attempt overwrite README.md",
            target_path="README.md",
            content="# Hacked README"
        )
        with self.assertRaises(ProtectedFileError):
            self.executor.execute(self.repo_dir, intent)
        # Verify content was untouched
        self.assertIn("Original README", self.readme_path.read_text(encoding="utf-8"))

    @patch.object(WriteExecutor, "_git_commit_and_push", return_value="abc1234")
    def test_normal_file_write_and_diff(self, mock_git):
        intent = IntentAnalysisResult(
            task_type=TaskType.WRITE,
            target_repo="gem-bridge",
            summary="Update feature.py",
            target_path="src/feature.py",
            content="def new_function(): return True\n",
            commit_message="feat: update feature"
        )
        result = self.executor.execute(self.repo_dir, intent)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["commit_hash"], "abc1234")
        self.assertIn("-def old_function(): pass", result["diff"])
        self.assertIn("+def new_function(): return True", result["diff"])
        mock_git.assert_called_once_with(self.repo_dir, "src/feature.py", "feat: update feature")

    def test_directory_traversal_blocked(self):
        intent = IntentAnalysisResult(
            task_type=TaskType.WRITE,
            target_repo="gem-bridge",
            summary="Directory traversal attempt",
            target_path="../../evil.txt",
            content="evil"
        )
        with self.assertRaises(PermissionError):
            self.executor.execute(self.repo_dir, intent)

    @patch.object(WriteExecutor, "_git_commit_and_push", return_value="mv12345")
    def test_file_move_and_synthesize_code(self, mock_git):
        # 1. Setup root index.html
        root_html = self.repo_dir / "index.html"
        root_html.write_text('<html><a href="README.md">Doc</a></html>', encoding="utf-8")

        mock_gemini = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '<html><a href="../README.md">Doc</a></html>'
        mock_gemini.models.generate_content.return_value = mock_response

        executor = WriteExecutor(gemini_client=mock_gemini)
        intent = IntentAnalysisResult(
            task_type=TaskType.WRITE,
            target_repo="gem-bridge",
            summary="Move index.html to docs/ and fix links",
            source_path="index.html",
            target_path="docs/index.html",
            instruction="루트의 index.html을 docs/index.html로 이동하고 상대 경로 수정",
            commit_message="docs: move index.html to docs/"
        )

        result = executor.execute(self.repo_dir, intent)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["commit_hash"], "mv12345")
        self.assertFalse(root_html.exists(), "Original source file should be removed upon move")
        new_file = self.repo_dir / "docs" / "index.html"
        self.assertTrue(new_file.exists())
        self.assertEqual(new_file.read_text(encoding="utf-8"), '<html><a href="../README.md">Doc</a></html>')
        self.assertIn("docs/index.html", result["target_path"])

    @patch.object(WriteExecutor, "_git_commit_and_push", return_value="synth99")
    def test_synthesize_code_when_content_missing(self, mock_git):
        # Setup file
        calc_path = self.repo_dir / "calc.py"
        calc_path.write_text("def add(a, b): return a - b\n", encoding="utf-8")

        mock_gemini = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "```python\ndef add(a, b): return a + b\n```"
        mock_gemini.models.generate_content.return_value = mock_response

        executor = WriteExecutor(gemini_client=mock_gemini)
        intent = IntentAnalysisResult(
            task_type=TaskType.WRITE,
            target_repo="gem-bridge",
            summary="Fix add function bug",
            target_path="calc.py",
            instruction="Fix bug in add function",
            commit_message="fix: add should sum"
        )

        result = executor.execute(self.repo_dir, intent)
        self.assertEqual(result["status"], "success")
        self.assertEqual(calc_path.read_text(encoding="utf-8"), "def add(a, b): return a + b\n")
        self.assertIn("-def add(a, b): return a - b", result["diff"])
        self.assertIn("+def add(a, b): return a + b", result["diff"])


    def test_synthesize_code_tiered_fallback(self):
        mock_gemini = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "def add(a, b): return a + b\n"

        # 3.8-flash fails with 503, 3.7-flash succeeds
        mock_gemini.models.generate_content.side_effect = [
            RuntimeError("503 UNAVAILABLE"),
            mock_response
        ]

        executor = WriteExecutor(gemini_client=mock_gemini)
        synthesized = executor.synthesize_code(
            original_text="def add(a, b): return a - b\n",
            instruction="Fix bug in add function",
            target_path="calc.py"
        )
        self.assertEqual(synthesized, "def add(a, b): return a + b\n")
        self.assertEqual(mock_gemini.models.generate_content.call_count, 2)
        first_call = mock_gemini.models.generate_content.call_args_list[0][1]
        second_call = mock_gemini.models.generate_content.call_args_list[1][1]
        self.assertEqual(first_call["model"], "gemini-3.8-flash")
        self.assertIsNotNone(first_call.get("config"))
        self.assertEqual(first_call["config"].thinking_config.thinking_budget, 0)
        self.assertEqual(second_call["model"], "gemini-3.7-flash")
        self.assertIsNotNone(second_call.get("config"))
        self.assertEqual(second_call["config"].thinking_config.thinking_budget, 0)

    def test_synthesize_code_38_flash_passes_thinking_budget_zero(self):
        mock_gemini = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "def add(a, b): return a + b\n"
        mock_gemini.models.generate_content.return_value = mock_response

        executor = WriteExecutor(gemini_client=mock_gemini)
        synthesized = executor.synthesize_code(
            original_text="def add(a, b): return a - b\n",
            instruction="Fix bug in add function",
            target_path="calc.py"
        )
        self.assertEqual(synthesized, "def add(a, b): return a + b\n")
        self.assertEqual(mock_gemini.models.generate_content.call_count, 1)
        first_call = mock_gemini.models.generate_content.call_args_list[0][1]
        self.assertEqual(first_call["model"], "gemini-3.8-flash")
        self.assertIsNotNone(first_call.get("config"))
        self.assertEqual(first_call["config"].thinking_config.thinking_budget, 0)

    def test_synthesize_code_prefers_user_gemini_client(self):
        mock_user_gemini = MagicMock()
        mock_api_gemini = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "def add(a, b): return a + b\n"
        mock_user_gemini.models.generate_content.return_value = mock_response

        executor = WriteExecutor(
            gemini_client=mock_api_gemini,
            user_gemini_client=mock_user_gemini
        )
        result = executor.synthesize_code(
            original_text="def add(a, b): return a - b\n",
            instruction="Fix bug in add function",
            target_path="calc.py"
        )
        self.assertEqual(result, "def add(a, b): return a + b\n")
        self.assertEqual(mock_user_gemini.models.generate_content.call_count, 1)
        first_call = mock_user_gemini.models.generate_content.call_args_list[0][1]
        self.assertEqual(first_call["model"], "gemini-3.8-flash")
        # Ensure API Key client was NOT needed
        self.assertEqual(mock_api_gemini.models.generate_content.call_count, 0)

    def test_synthesize_code_cascades_from_user_client_to_api_key_client(self):
        mock_user_gemini = MagicMock()
        mock_api_gemini = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "def add(a, b): return a + b\n"

        # User client fails (e.g. 429 RPM limit)
        mock_user_gemini.models.generate_content.side_effect = RuntimeError("429 RESOURCE_EXHAUSTED")
        mock_api_gemini.models.generate_content.return_value = mock_response

        executor = WriteExecutor(
            gemini_client=mock_api_gemini,
            user_gemini_client=mock_user_gemini
        )
        result = executor.synthesize_code(
            original_text="def add(a, b): return a - b\n",
            instruction="Fix bug in add function",
            target_path="calc.py"
        )
        self.assertEqual(result, "def add(a, b): return a + b\n")
        # User client tried 3.8-flash once
        self.assertEqual(mock_user_gemini.models.generate_content.call_count, 1)
        # API Key client picked up and executed
        self.assertEqual(mock_api_gemini.models.generate_content.call_count, 1)
        api_call = mock_api_gemini.models.generate_content.call_args_list[0][1]
        self.assertEqual(api_call["model"], "gemini-3.8-flash")


if __name__ == "__main__":
    unittest.main()

