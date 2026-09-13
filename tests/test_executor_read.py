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

        # 3.8-flash fails, 3.7-flash succeeds
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
        self.assertIsNotNone(first_call.get("config"))
        self.assertEqual(first_call["config"].thinking_config.thinking_budget, 0)
        self.assertEqual(second_call["model"], "gemini-3.7-flash")
        self.assertIsNotNone(second_call.get("config"))
        self.assertEqual(second_call["config"].thinking_config.thinking_budget, 0)

    def test_generate_report_prefers_user_gemini_client(self):
        mock_user_gemini = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "# [보고서] User OAuth 심층 분석 결과"
        mock_user_gemini.models.generate_content.return_value = mock_response

        executor = ReadExecutor(
            drive_service=self.mock_drive,
            gemini_client=self.mock_gemini,
            user_gemini_client=mock_user_gemini
        )

        intent = IntentAnalysisResult(
            task_type=TaskType.READ,
            summary="심층 분석 요청",
            target_repo="test-repo",
            query="구조 분석",
            model_tier="deep"
        )

        result = executor.execute(self.repo_dir, intent, original_title="!분석 test-repo")
        self.assertEqual(result["status"], "success")
        self.assertIn("User OAuth 심층 분석 결과", result["report"])
        self.assertEqual(mock_user_gemini.models.generate_content.call_count, 1)
        first_call = mock_user_gemini.models.generate_content.call_args_list[0][1]
        self.assertEqual(first_call["model"], "gemini-3.8-flash")
        # Ensure API Key client was NOT called
        self.assertEqual(self.mock_gemini.models.generate_content.call_count, 0)

    def test_generate_report_cascades_from_user_client_to_api_key_client(self):
        mock_user_gemini = MagicMock()
        mock_user_gemini.models.generate_content.side_effect = RuntimeError("429 RESOURCE_EXHAUSTED")

        mock_api_response = MagicMock()
        mock_api_response.text = "# [보고서] API Key 폴백 분석 결과"
        self.mock_gemini.models.generate_content.return_value = mock_api_response

        executor = ReadExecutor(
            drive_service=self.mock_drive,
            gemini_client=self.mock_gemini,
            user_gemini_client=mock_user_gemini
        )

        intent = IntentAnalysisResult(
            task_type=TaskType.READ,
            summary="심층 분석 요청",
            target_repo="test-repo",
            query="구조 분석",
            model_tier="deep"
        )

        result = executor.execute(self.repo_dir, intent, original_title="!분석 test-repo")
        self.assertEqual(result["status"], "success")
        self.assertIn("API Key 폴백 분석 결과", result["report"])
        # User client tried 3.8-flash once
        self.assertEqual(mock_user_gemini.models.generate_content.call_count, 1)
        # API Key client picked up and executed
        self.assertEqual(self.mock_gemini.models.generate_content.call_count, 1)
        api_call = self.mock_gemini.models.generate_content.call_args_list[0][1]
        self.assertEqual(api_call["model"], "gemini-3.8-flash")

    def test_generate_report_prefers_tier0_codeassist_client(self):
        mock_codeassist = MagicMock()
        mock_codeassist.is_available.return_value = True
        mock_codeassist.generate_content.return_value = "# CodeAssist 보고서\n- 정상 완료"
        mock_user_gemini = MagicMock()

        executor = ReadExecutor(
            drive_service=self.mock_drive,
            gemini_client=self.mock_gemini,
            user_gemini_client=mock_user_gemini,
            codeassist_client=mock_codeassist
        )

        intent = IntentAnalysisResult(
            task_type=TaskType.READ,
            summary="심층 분석 요청",
            target_repo="test-repo",
            query="구조 분석",
            model_tier="deep"
        )

        result = executor.execute(self.repo_dir, intent, original_title="!분석 test-repo")
        self.assertEqual(result["status"], "success")
        self.assertIn("CodeAssist 보고서", result["report"])
        self.assertEqual(mock_codeassist.generate_content.call_count, 1)
        self.assertEqual(mock_user_gemini.models.generate_content.call_count, 0)
        self.assertEqual(self.mock_gemini.models.generate_content.call_count, 0)

    def test_generate_report_cascades_from_codeassist_to_user_oauth(self):
        mock_codeassist = MagicMock()
        mock_codeassist.is_available.return_value = True
        mock_codeassist.generate_content.side_effect = RuntimeError("CodeAssist 503 Unavailable")
        mock_user_gemini = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "# User OAuth 3.8 보고서\n- 정상 완료"
        mock_user_gemini.models.generate_content.return_value = mock_response

        executor = ReadExecutor(
            drive_service=self.mock_drive,
            gemini_client=self.mock_gemini,
            user_gemini_client=mock_user_gemini,
            codeassist_client=mock_codeassist
        )

        intent = IntentAnalysisResult(
            task_type=TaskType.READ,
            summary="심층 분석 요청",
            target_repo="test-repo",
            query="구조 분석",
            model_tier="deep"
        )

        result = executor.execute(self.repo_dir, intent, original_title="!분석 test-repo")
        self.assertEqual(result["status"], "success")
        self.assertIn("User OAuth 3.8 보고서", result["report"])
        self.assertEqual(mock_codeassist.generate_content.call_count, 1)
        self.assertEqual(mock_user_gemini.models.generate_content.call_count, 1)
        self.assertEqual(self.mock_gemini.models.generate_content.call_count, 0)

    def test_read_hero_file_mode_and_document_briefing(self):
        family_dir = self.repo_dir / "family"
        family_dir.mkdir(parents=True, exist_ok=True)
        memory_file = family_dir / "jinmok-odyssey-memory.md"
        memory_file.write_text("# 진목 오디세이 회고록\n가족 서사 기록 본문입니다.", encoding="utf-8")

        intent = IntentAnalysisResult(
            task_type=TaskType.READ,
            target_repo="test-repo",
            summary="진목 오디세이 내용 요약",
            query="진목 오디세이 내용 알려줘",
            target_files_or_dirs=["jinmok-odyssey-memory.md"]
        )

        context, candidate_files = self.executor._gather_repo_context(
            self.repo_dir, intent.target_files_or_dirs, return_files=True
        )
        self.assertEqual(len(candidate_files), 1)
        self.assertEqual(candidate_files[0], memory_file)
        self.assertIn("단일 대상 파일 집중 모드 (Hero File Mode)", context)
        self.assertIn("진목 오디세이 회고록", context)
        # Tree should be suppressed in hero mode
        self.assertNotIn("├── app.py", context)

        mode = self.executor._determine_read_mode(intent, candidate_files)
        self.assertEqual(mode, "DOCUMENT_BRIEFING")

        # Execute and check prompt sent to Gemini
        self.executor.execute(self.repo_dir, intent, original_title="!분석 진목 오디세이")
        gemini_call = self.mock_gemini.models.generate_content.call_args[1]
        prompt_used = gemini_call["contents"]
        self.assertIn("리서치 분석가 및 도큐먼트 전문가", prompt_used)
        self.assertIn("핵심 요약 (Executive Summary)", prompt_used)

    def test_read_code_explain_mode(self):
        intent = IntentAnalysisResult(
            task_type=TaskType.READ,
            target_repo="test-repo",
            summary="app.py 모듈 분석",
            query="app.py 역할 설명해줘",
            target_files_or_dirs=["src/app.py"]
        )

        context, candidate_files = self.executor._gather_repo_context(
            self.repo_dir, intent.target_files_or_dirs, return_files=True
        )
        mode = self.executor._determine_read_mode(intent, candidate_files)
        self.assertEqual(mode, "CODE_EXPLAIN")

        self.executor.execute(self.repo_dir, intent)
        gemini_call = self.mock_gemini.models.generate_content.call_args[1]
        prompt_used = gemini_call["contents"]
        self.assertIn("시니어 소프트웨어 엔지니어", prompt_used)
        self.assertIn("모듈 개요 및 주요 책임", prompt_used)

    def test_read_missing_file_warning_no_silent_fallback(self):
        intent = IntentAnalysisResult(
            task_type=TaskType.READ,
            target_repo="test-repo",
            summary="존재하지 않는 파일 조회",
            query="missing.md 내용 요약해줘",
            target_files_or_dirs=["missing.md"]
        )

        context, candidate_files = self.executor._gather_repo_context(
            self.repo_dir, intent.target_files_or_dirs, return_files=True
        )
        self.assertEqual(len(candidate_files), 0)
        self.assertIn("경고: 요청 파일 미발견", context)
        self.assertIn("missing.md", context)
        # Should NOT silently include README.md content when a specific target was missing
        self.assertNotIn("Sample documentation", context)


if __name__ == "__main__":
    unittest.main()
