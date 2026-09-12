import unittest
from core.console_protocol import ConsoleProtocolParser, ConsoleDocFormatter


class TestConsoleProtocol(unittest.TestCase):

    def test_extract_command_standard(self):
        raw = """# 📱 gem-bridge CONSOLE
>>> INPUT >>>
!작업 kum landing page 업데이트
<<< END <<<
## 📤 [CONSOLE OUTPUT]
이전 결과
"""
        cmd = ConsoleProtocolParser.extract_command(raw)
        self.assertEqual(cmd, "!작업 kum landing page 업데이트")

    def test_extract_command_dirty_mobile_input(self):
        # Typographic quotes, em-dash, BOM, zero-width space
        raw = "\ufeff>>> INPUT >>>\n\u200b!작업 “kum” ‘src/index.html’ — 내용 변경\n<<< END <<<"
        cmd = ConsoleProtocolParser.extract_command(raw)
        self.assertEqual(cmd, '!작업 "kum" \'src/index.html\' - 내용 변경')

    def test_extract_command_with_markdown_decorations(self):
        raw = "**>>> INPUT >>>**\n`!분석 gem-bridge 아키텍처`\n**<<< END <<<**"
        cmd = ConsoleProtocolParser.extract_command(raw)
        self.assertEqual(cmd, "`!분석 gem-bridge 아키텍처`")

    def test_extract_command_missing_end_tag(self):
        raw = """>>> INPUT >>>
!실행 kum npm test
══════════════════════════════════════════════════════════════
## 📤 [CONSOLE OUTPUT]
"""
        cmd = ConsoleProtocolParser.extract_command(raw)
        self.assertEqual(cmd, "!실행 kum npm test")

    def test_extract_command_alternative_tag(self):
        raw = """[COMMAND]
!작업 tetris-loop 점수 시스템 개선
[/COMMAND]
"""
        cmd = ConsoleProtocolParser.extract_command(raw)
        self.assertEqual(cmd, "!작업 tetris-loop 점수 시스템 개선")

    def test_ignore_empty_or_placeholder_commands(self):
        raw1 = ">>> INPUT >>>\n\n<<< END <<<"
        self.assertIsNone(ConsoleProtocolParser.extract_command(raw1))

        raw2 = ">>> INPUT >>>\n(여기에 작업을 입력하세요. 예: !작업 kum 랜딩페이지 개선)\n<<< END <<<"
        self.assertIsNone(ConsoleProtocolParser.extract_command(raw2))

        raw3 = ">>> INPUT >>>\n(Write your command here)\n<<< END <<<"
        self.assertIsNone(ConsoleProtocolParser.extract_command(raw3))

    def test_anti_echo_hash_stability(self):
        text1 = "line 1\r\nline 2\r\n"
        text2 = "line 1\nline 2\n"
        # Should produce identical hash after normalization
        self.assertEqual(
            ConsoleProtocolParser.compute_content_hash(text1),
            ConsoleProtocolParser.compute_content_hash(text2)
        )

        cmd1 = "  !작업   kum   랜딩페이지   "
        cmd2 = "!작업 kum 랜딩페이지"
        self.assertEqual(
            ConsoleProtocolParser.compute_command_hash(cmd1),
            ConsoleProtocolParser.compute_command_hash(cmd2)
        )

    def test_formatter_render_and_history(self):
        history = [
            "- [2026-09-12 15:00 KST] [✅ 완료] !작업 kum 랜딩페이지 (커밋 a1b2c3d)",
            "- [2026-09-12 14:30 KST] [📄 분석] !분석 gem-bridge 구조",
        ]
        rendered = ConsoleDocFormatter.render(
            status="ONLINE",
            input_command="!작업 kum 수정",
            output_content="성공적으로 커밋되었습니다.",
            history_items=history,
            timestamp_str="2026-09-12 16:00:00"
        )
        self.assertIn("🟢 ONLINE (통신: 2026-09-12 16:00:00 KST)", rendered)
        self.assertIn("!작업 kum 수정", rendered)
        self.assertIn("성공적으로 커밋되었습니다.", rendered)
        self.assertIn("!작업 kum 랜딩페이지 (커밋 a1b2c3d)", rendered)

    def test_formatter_offline_badge(self):
        rendered = ConsoleDocFormatter.render(
            status="OFFLINE",
            timestamp_str="2026-09-12 16:05:00"
        )
        self.assertIn("🔴 OFFLINE", rendered)
        self.assertIn("현재 PC가 꺼져 있습니다", rendered)

    def test_formatter_with_trace_id_and_latency(self):
        rendered = ConsoleDocFormatter.render(
            status="ONLINE",
            input_command="!작업 kum 수정",
            output_content="성공적으로 커밋되었습니다.",
            trace_id="tsk_20260912162000_abcd",
            duration_summary="2.15s [drive: 200ms | git: 1950ms]",
            sync_lag_ms=1200.5
        )
        self.assertIn("tsk_20260912162000_abcd", rendered)
        self.assertIn("2.15s [drive: 200ms | git: 1950ms]", rendered)
        self.assertIn("1200ms", rendered)
        self.assertIn("동기화 지연", rendered)

    def test_formatter_action_status_banners(self):
        # 1. Commit Success Banner
        r1 = ConsoleDocFormatter.render(
            status="ONLINE",
            action_status="COMMIT_SUCCESS",
            action_message="index.html ➔ docs/index.html 이동 및 Git Push 완료",
            output_content="커밋 내용"
        )
        self.assertIn("🟢 **[작업 완료 / Git 반영]**", r1)
        self.assertIn("index.html ➔ docs/index.html 이동 및 Git Push 완료", r1)

        # 2. Guardrail Redirect Banner
        r2 = ConsoleDocFormatter.render(
            status="ONLINE",
            action_status="GUARDRAIL_REDIRECT",
            action_message="파일 수정 코드가 명시되지 않아 안전 가드레일에 의해 [분석 보고서]로 대체되었습니다.",
            output_content="보고서 요약"
        )
        self.assertIn("🟡 **[가드레일 작동 / 분석 대체]**", r2)
        self.assertIn("안전 가드레일에 의해 [분석 보고서]로 대체되었습니다", r2)

        # 3. Error Banner
        r3 = ConsoleDocFormatter.render(
            status="ONLINE",
            action_status="ERROR",
            action_message="지정된 리포지토리를 찾을 수 없습니다.",
            output_content="오류 내용"
        )
        self.assertIn("🔴 **[작업 실패 / 오류]**", r3)
        self.assertIn("지정된 리포지토리를 찾을 수 없습니다", r3)


if __name__ == "__main__":
    unittest.main()
