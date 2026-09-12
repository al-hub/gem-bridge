import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from core.session_manager import (
    TurnType,
    Turn,
    PinnedFacts,
    SessionState,
    SessionPruner,
    SessionStore,
    SessionCompactor,
    SessionManager,
)


class TestSessionPruner(unittest.TestCase):
    """Tests for Seam 1: SessionPruner (Diff and Log Trimming)."""

    def test_prune_diff_small_diff_retained(self):
        small_diff = (
            "diff --git a/core/calc.py b/core/calc.py\n"
            "--- a/core/calc.py\n"
            "+++ b/core/calc.py\n"
            "@@ -1,3 +1,4 @@\n"
            "+def add(a, b):\n"
            "+    return a + b\n"
        )
        result = SessionPruner.prune_diff(small_diff, max_lines=50)
        self.assertEqual(result, small_diff.strip())

    def test_prune_diff_large_diff_truncated_to_max_lines(self):
        # Generate 120 lines of diff
        lines = ["diff --git a/big.py b/big.py", "@@ -1,100 +1,120 @@"]
        for i in range(118):
            lines.append(f"+line_{i} = {i}")
        big_diff = "\n".join(lines)

        result = SessionPruner.prune_diff(big_diff, max_lines=30)
        self.assertIsNotNone(result)
        result_lines = result.splitlines()
        self.assertLessEqual(len(result_lines), 31)  # 30 lines + 1 truncation marker
        self.assertTrue(any("생략" in line or "truncated" in line.lower() for line in result_lines))

    def test_prune_diff_empty_or_none(self):
        self.assertIsNone(SessionPruner.prune_diff(None))
        self.assertIsNone(SessionPruner.prune_diff(""))
        self.assertIsNone(SessionPruner.prune_diff("   \n  "))

    def test_prune_stdout_success_returns_compact_tail(self):
        long_success_stdout = "\n".join([f"Step {i}: processing..." for i in range(50)] + ["15 passed in 0.45s"])
        result = SessionPruner.prune_stdout(long_success_stdout, exit_code=0)
        self.assertIsNotNone(result)
        self.assertIn("15 passed in 0.45s", result)
        self.assertLessEqual(len(result.splitlines()), 3)

    def test_prune_stdout_failure_extracts_traceback(self):
        failure_stdout = (
            "Building project...\n"
            "Compiling...\n"
            "Traceback (most recent call last):\n"
            '  File "test_auth.py", line 42, in test_login\n'
            "    assert token is not None\n"
            "AssertionError: assert None is not None\n"
        )
        result = SessionPruner.prune_stdout(failure_stdout, exit_code=1)
        self.assertIsNotNone(result)
        self.assertIn("AssertionError", result)
        self.assertIn("Traceback", result)

    def test_prune_stdout_empty_or_none(self):
        self.assertIsNone(SessionPruner.prune_stdout(None, exit_code=0))
        self.assertIsNone(SessionPruner.prune_stdout("", exit_code=0))


class TestSessionStore(unittest.TestCase):
    """Tests for Seam 2: SessionStore (SQLite WAL Persistence & Recovery)."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.store = SessionStore(storage_dir=self.temp_dir, ttl_seconds=1800.0)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_create_and_load_session(self):
        session = self.store.get_or_create_session(
            channel="tasks",
            target_repo="gem-bridge",
            session_key="tasks:gem-bridge"
        )
        self.assertIsNotNone(session)
        self.assertEqual(session.session_key, "tasks:gem-bridge")
        self.assertEqual(session.target_repo, "gem-bridge")
        self.assertEqual(session.channel, "tasks")
        self.assertTrue(session.is_active)

        # Re-load from store
        loaded = self.store.load_active_session("tasks:gem-bridge")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.session_id, session.session_id)
        self.assertEqual(loaded.target_repo, "gem-bridge")

    def test_record_turn_and_restore_history(self):
        session = self.store.get_or_create_session(
            channel="tasks",
            target_repo="remote_codex",
            session_key="tasks:remote_codex"
        )

        turn1 = Turn(
            turn_index=1,
            task_type=TurnType.READ,
            user_raw_input="auth/token.py 분석해줘",
            summary="token.py 만료 버그 분석",
            target_path="auth/token.py",
            execution_preview="분석 완료: 15분 만료 로직 확인"
        )
        self.store.record_turn(session.session_key, turn1)

        turn2 = Turn(
            turn_index=2,
            task_type=TurnType.WRITE,
            user_raw_input="만료 시간을 30분으로 늘려줘",
            summary="token.py 만료 시간 연장",
            target_path="auth/token.py",
            commit_hash="a1b2c3d4e5f6",
            pruned_diff="+ EXPIRY = 1800\n- EXPIRY = 900"
        )
        self.store.record_turn(session.session_key, turn2)

        # Simulate daemon restart by loading freshly from store
        fresh_store = SessionStore(storage_dir=self.temp_dir, ttl_seconds=1800.0)
        restored_session = fresh_store.load_active_session("tasks:remote_codex")
        self.assertIsNotNone(restored_session)
        self.assertEqual(len(restored_session.turns), 2)
        self.assertEqual(restored_session.turns[0].summary, "token.py 만료 버그 분석")
        self.assertEqual(restored_session.turns[1].commit_hash, "a1b2c3d4e5f6")
        self.assertIn("auth/token.py", restored_session.pinned_facts.touched_files)
        self.assertEqual(restored_session.pinned_facts.last_commit_hash, "a1b2c3d4e5f6")

    def test_session_ttl_expiration(self):
        # Create a store with 1 second TTL
        quick_store = SessionStore(storage_dir=self.temp_dir, ttl_seconds=0.1)
        session = quick_store.get_or_create_session(
            channel="tasks",
            target_repo="test-repo",
            session_key="tasks:test-repo"
        )
        self.assertIsNotNone(session)

        time.sleep(0.15)
        # Should be expired
        loaded = quick_store.load_active_session("tasks:test-repo")
        self.assertIsNone(loaded)

    def test_archive_session(self):
        session = self.store.get_or_create_session(
            channel="console",
            target_repo="gem-bridge",
            session_key="console:gem-bridge"
        )
        self.assertTrue(session.is_active)

        self.store.archive_session("console:gem-bridge")
        loaded = self.store.load_active_session("console:gem-bridge")
        self.assertIsNone(loaded)


class TestSessionCompactor(unittest.TestCase):
    """Tests for Seam 3: SessionCompactor (Sliding Window & Rolling Summary)."""

    def test_sliding_window_compaction(self):
        compactor = SessionCompactor(sliding_window_n=2)
        session = SessionState(
            session_id="test_sess",
            session_key="tasks:gem-bridge",
            target_repo="gem-bridge"
        )

        turn1 = Turn(turn_index=1, task_type=TurnType.READ, user_raw_input="분석해줘", summary="1단계 분석 완료")
        turn2 = Turn(turn_index=2, task_type=TurnType.WRITE, user_raw_input="코드 수정해줘", summary="2단계 수정 완료")

        # Within sliding window (N=2)
        compactor.compact_if_needed(session, [turn1, turn2])
        self.assertEqual(session.rolling_summary, "")

        # Turn 3 exceeds sliding window N=2 -> Turn 1 must be folded into rolling_summary
        turn3 = Turn(turn_index=3, task_type=TurnType.EXEC, user_raw_input="테스트 돌려줘", summary="3단계 테스트 성공")
        remaining_turns = compactor.compact_if_needed(session, [turn1, turn2, turn3])

        self.assertEqual(len(remaining_turns), 2)
        self.assertEqual(remaining_turns[0].summary, "2단계 수정 완료")
        self.assertEqual(remaining_turns[1].summary, "3단계 테스트 성공")
        self.assertIn("1단계 분석 완료", session.rolling_summary)

        # Turn 4 -> Turn 2 also folded
        turn4 = Turn(turn_index=4, task_type=TurnType.WRITE, user_raw_input="커밋해줘", summary="4단계 커밋 완료")
        remaining_turns2 = compactor.compact_if_needed(session, [remaining_turns[0], remaining_turns[1], turn4])
        self.assertEqual(len(remaining_turns2), 2)
        self.assertEqual(remaining_turns2[0].summary, "3단계 테스트 성공")
        self.assertEqual(remaining_turns2[1].summary, "4단계 커밋 완료")
        self.assertIn("1단계 분석 완료", session.rolling_summary)
        self.assertIn("2단계 수정 완료", session.rolling_summary)


class TestSessionManager(unittest.TestCase):
    """Tests for Seam 4: SessionManager (Deep Unified Public Seam)."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.manager = SessionManager(storage_dir=self.temp_dir, ttl_seconds=1800.0)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_or_resume_session_maintains_continuity(self):
        s1 = self.manager.get_or_resume_session(
            channel="tasks",
            target_repo="gem-bridge",
            incoming_input="auth 로직 분석해줘"
        )
        self.assertIsNotNone(s1)

        # Second request on the same repo continues the same session
        s2 = self.manager.get_or_resume_session(
            channel="tasks",
            target_repo="gem-bridge",
            incoming_input="방금 분석한 토큰 로직 수정해줘"
        )
        self.assertEqual(s1.session_id, s2.session_id)

    def test_explicit_reset_triggers_fresh_session(self):
        s1 = self.manager.get_or_resume_session(
            channel="tasks",
            target_repo="gem-bridge",
            incoming_input="작업 시작"
        )
        self.manager.record_turn(
            session=s1,
            task_type=TurnType.WRITE,
            user_input="작업 시작",
            summary="초기 파일 생성",
            target_path="main.py"
        )

        # Reset keyword triggers new session
        s2 = self.manager.get_or_resume_session(
            channel="tasks",
            target_repo="gem-bridge",
            incoming_input="!새작업 완전히 다른 프로젝트 작업하자"
        )
        self.assertNotEqual(s1.session_id, s2.session_id)
        self.assertEqual(len(s2.turns), 0)

    def test_build_replay_context_formatting(self):
        session = self.manager.get_or_resume_session(
            channel="tasks",
            target_repo="remote_codex",
            incoming_input="분석해줘"
        )
        self.manager.record_turn(
            session=session,
            task_type=TurnType.WRITE,
            user_input="로그인 함수 버그 수정",
            summary="login() 예외처리 추가",
            target_path="auth/login.py",
            commit_hash="deadbeef1234",
            raw_diff="+ try:\n+     login()\n+ except Exception:\n+     pass"
        )

        context_text = self.manager.build_replay_context(session)
        self.assertIn("불변 고정 메타데이터", context_text)
        self.assertIn("remote_codex", context_text)
        self.assertIn("auth/login.py", context_text)
        self.assertIn("deadbee", context_text)
        self.assertIn("직전 연속 작업 세부 맥락", context_text)

    def test_check_git_drift(self):
        session = self.manager.get_or_resume_session(
            channel="tasks",
            target_repo="remote_codex",
            incoming_input="작업 시작"
        )
        self.manager.record_turn(
            session=session,
            task_type=TurnType.WRITE,
            user_input="커밋 생성",
            summary="1차 커밋",
            commit_hash="aaaa1111"
        )

        # No drift when head matches
        no_drift = self.manager.check_git_drift(session, current_git_head="aaaa1111")
        self.assertIsNone(no_drift)

        # Drift detected when external checkout/reset changed head
        drift_note = self.manager.check_git_drift(session, current_git_head="bbbb2222")
        self.assertIsNotNone(drift_note)
        self.assertIn("외부 Git 상태 변경 감지", drift_note)


if __name__ == "__main__":
    unittest.main()

