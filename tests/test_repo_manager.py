import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from core.repo_manager import RepoManager, RepoError


class TestRepoManager(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_repos_dir = Path(self.temp_dir.name) / "repos"
        self.dummy_local_repo = Path(self.temp_dir.name) / "my-local-repo"
        self.dummy_local_repo.mkdir(parents=True, exist_ok=True)
        (self.dummy_local_repo / ".git").mkdir()

        self.repo_mapping = {
            "gem-bridge": str(self.dummy_local_repo),
            "custom-app": str(self.dummy_local_repo),
        }
        self.manager = RepoManager(
            repos_base_dir=self.base_repos_dir,
            repo_mapping=self.repo_mapping
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_is_public_url(self):
        self.assertTrue(self.manager.is_public_url("https://github.com/google/gemini.git"))
        self.assertTrue(self.manager.is_public_url("http://gitlab.com/test/repo"))
        self.assertTrue(self.manager.is_public_url("git@github.com:user/project.git"))
        self.assertFalse(self.manager.is_public_url("gem-bridge"))
        self.assertFalse(self.manager.is_public_url("/local/path/to/repo"))

    def test_extract_repo_name_from_url(self):
        self.assertEqual(
            self.manager.extract_repo_name_from_url("https://github.com/google/gemini.git"),
            "gemini"
        )
        self.assertEqual(
            self.manager.extract_repo_name_from_url("git@github.com:owner/my_cool_project.git"),
            "my_cool_project"
        )

    def test_prepare_local_repo_success(self):
        prepared = self.manager.prepare_repo("gem-bridge")
        self.assertEqual(prepared, self.dummy_local_repo)

    def test_prepare_local_repo_fallback_to_gem_bridge(self):
        # Unknown repo falls back to gem-bridge
        prepared = self.manager.prepare_repo("unknown-repo")
        self.assertEqual(prepared, self.dummy_local_repo)

    def test_prepare_local_repo_not_found_without_fallback(self):
        empty_mgr = RepoManager(repo_mapping={})
        with self.assertRaises(RepoError):
            empty_mgr.prepare_repo("non-existent-repo")

    @patch("subprocess.run")
    def test_prepare_public_repo_shallow_clone(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(stdout="Cloned", returncode=0)
        public_url = "https://github.com/example/sample-lib.git"
        
        target = self.manager.prepare_repo(public_url)
        self.assertEqual(target.name, "sample-lib")
        mock_subprocess.assert_called_once()
        args, kwargs = mock_subprocess.call_args
        self.assertIn("clone", args[0])
        self.assertIn("--depth", args[0])
        self.assertIn("1", args[0])
        self.assertIn(public_url, args[0])


if __name__ == "__main__":
    unittest.main()
