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


if __name__ == "__main__":
    unittest.main()
