import tempfile
import unittest
from pathlib import Path
from core.path_resolver import RepoPathEngine


class TestPathResolver(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_dir.name)

        # Setup directory structure
        (self.repo_dir / "README.md").write_text("# Root README\n", encoding="utf-8")
        (self.repo_dir / "index.html").write_text("<html>root</html>\n", encoding="utf-8")

        family_dir = self.repo_dir / "family"
        family_dir.mkdir(parents=True, exist_ok=True)
        self.hero_file = family_dir / "jinmok-odyssey-memory.md"
        self.hero_file.write_text("# Jinmok Odyssey Memory\nFamily chronicle.", encoding="utf-8")

        src_dir = self.repo_dir / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "app_main.py").write_text("print('app')", encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_exact_resolution(self):
        resolved = RepoPathEngine.resolve(self.repo_dir, "README.md")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved, self.repo_dir / "README.md")

    def test_fuzzy_recursive_lookup_in_subdirectory(self):
        # User requested filename only without directory path
        resolved = RepoPathEngine.resolve(self.repo_dir, "jinmok-odyssey-memory.md")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved, self.hero_file)

    def test_extension_auto_completion(self):
        # User omitted .md extension
        resolved = RepoPathEngine.resolve(self.repo_dir, "jinmok-odyssey-memory")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved, self.hero_file)

    def test_delimiter_normalization(self):
        # User typed underscores instead of hyphens
        resolved = RepoPathEngine.resolve(self.repo_dir, "jinmok_odyssey_memory")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved, self.hero_file)

    def test_directory_structure_preservation_for_new_or_different_file(self):
        # docs/index.html should NOT resolve to root index.html
        resolved = RepoPathEngine.resolve(self.repo_dir, "docs/index.html")
        self.assertIsNone(resolved)

    def test_directory_traversal_prevention(self):
        # Attempt traversal outside repo
        resolved = RepoPathEngine.resolve(self.repo_dir, "../outside.txt")
        self.assertIsNone(resolved)

    def test_missing_file_returns_none(self):
        resolved = RepoPathEngine.resolve(self.repo_dir, "non_existent_file.md")
        self.assertIsNone(resolved)


if __name__ == "__main__":
    unittest.main()
