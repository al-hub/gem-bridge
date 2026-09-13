import json
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

    def test_fallback_read_preserves_mentioned_repo(self):
        with patch.object(self.analyzer.client.models, "generate_content", side_effect=Exception("API Timeout")):
            result = self.analyzer.analyze("repo: tetris-loop 에서 버그 조사", title="오류", available_repos=["kum", "tetris-loop", "gem-bridge"])
            self.assertEqual(result.task_type, TaskType.READ)
            self.assertEqual(result.target_repo, "tetris-loop")

    def test_key_value_content_start_parsing(self):
        raw_text = """\ufeffrepo: gem-bridge
target_path: index.html
commit_message: feat: update landing page
---CONTENT_START---
<!DOCTYPE html>
<html><body>Hello</body></html>
---CONTENT_END---
"""
        result = self.analyzer.analyze(raw_text, title="task.txt", available_repos=["kum", "gem-bridge"])
        self.assertEqual(result.task_type, TaskType.WRITE)
        self.assertEqual(result.target_repo, "gem-bridge")
        self.assertEqual(result.target_path, "index.html")
        self.assertEqual(result.commit_message, "feat: update landing page")
        self.assertIn("<!DOCTYPE html>", result.content)

    def test_natural_language_refactoring_preserves_write(self):
        # When target_path and instruction are present (even if content is null), WRITE is preserved!
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "task_type": "WRITE",
            "target_repo": "gem-bridge",
            "summary": "Move index.html to docs/ and adjust relative paths",
            "source_path": "index.html",
            "target_path": "docs/index.html",
            "content": None,
            "instruction": "루트의 index.html을 docs/index.html로 이동하고 상대 경로 수정",
            "commit_message": "docs: move index.html to docs/ and fix links"
        })

        with patch.object(self.analyzer.client.models, "generate_content", return_value=mock_response):
            result = self.analyzer._analyze_with_llm("!작업 index.html을 docs/로 이동", ["gem-bridge"])
            self.assertEqual(result.task_type, TaskType.WRITE)
            self.assertEqual(result.source_path, "index.html")
            self.assertEqual(result.target_path, "docs/index.html")
            self.assertIsNotNone(result.instruction)


    def test_analyze_with_session_context(self):
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "task_type": "WRITE",
            "target_repo": "gem-bridge",
            "summary": "Fix login bug in auth/login.py",
            "target_path": "auth/login.py",
            "instruction": "Fix bug in login function"
        })

        with patch.object(self.analyzer.client.models, "generate_content", return_value=mock_response) as mock_gen:
            result = self.analyzer.analyze(
                raw_text="그 파일의 버그 수정해줘",
                session_context="### 🔒 [불변 고정 메타데이터]\n- **대상 저장소**: `gem-bridge`\n- **최근 수정된 파일 목록**: `auth/login.py`\n"
            )
            self.assertEqual(result.task_type, TaskType.WRITE)
            self.assertEqual(result.target_path, "auth/login.py")
            called_kwargs = mock_gen.call_args[1] if mock_gen.call_args else {}
            called_contents = called_kwargs.get("contents", "")
            self.assertIn("불변 고정 메타데이터", called_contents)
            self.assertIn("auth/login.py", called_contents)

    def test_approval_interceptor_single_word(self):
        session_ctx = (
            "### 🔒 [불변 고정 메타데이터 (Pinned Facts)]\n"
            "- **대상 저장소**: `remote_codex`\n"
            "- **최근 수정된 파일 목록**: `src/auth.py`\n"
            "- **최신 커밋**: `7c4a1b2`\n"
        )
        # Even without mocking generate_content, ApprovalInterceptor resolves immediately
        result = self.analyzer.analyze(
            raw_text="승인",
            title="승인",
            available_repos=["remote_codex", "gem-bridge"],
            session_context=session_ctx
        )
        self.assertEqual(result.task_type, TaskType.WRITE)
        self.assertEqual(result.target_repo, "remote_codex")
        self.assertEqual(result.target_path, "src/auth.py")
        self.assertIn("ApprovalInterceptor", result.reasoning)

    def test_approval_interceptor_merge_phrase(self):
        session_ctx = (
            "### ⚡ [직전 연속 작업 세부 맥락]\n"
            "- **대상 저장소**: `remote_codex`\n"
            "- **대상 파일**: `src/auth.py`\n"
        )
        result = self.analyzer.analyze(
            raw_text="1번 머지해줘",
            title="1번 머지해줘",
            available_repos=["remote_codex", "gem-bridge"],
            session_context=session_ctx
        )
        self.assertEqual(result.task_type, TaskType.WRITE)
        self.assertEqual(result.target_repo, "remote_codex")
        self.assertEqual(result.target_path, "src/auth.py")


if __name__ == "__main__":
    unittest.main()


