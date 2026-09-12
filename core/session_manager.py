"""
core/session_manager.py
Fault-tolerant, persisted session continuity and context compression module
for the gem-bridge agent system.

Following SRP (Single Responsibility Principle):
- Turn / PinnedFacts / SessionState: Pure data models
- SessionPruner: Aggressive diff and terminal log trimming
- SessionStore: SQLite WAL persistence and instant recovery across restarts
- SessionCompactor: Sliding window and rolling summarization
- SessionManager: Deep unified public seam for callers
"""

import json
import logging
import os
import re
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("gem_bridge.session_manager")


class TurnType(str, Enum):
    READ = "READ"
    WRITE = "WRITE"
    EXEC = "EXEC"


@dataclass
class Turn:
    """Represents a single execution turn in a continuous session."""
    turn_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    turn_index: int = 1
    timestamp: float = field(default_factory=time.time)
    task_type: TurnType = TurnType.READ
    user_raw_input: str = ""
    summary: str = ""
    target_path: Optional[str] = None
    commit_hash: Optional[str] = None
    exit_code: Optional[int] = None
    execution_preview: str = ""
    pruned_diff: Optional[str] = None
    pruned_stdout: Optional[str] = None

    def to_replay_text(self) -> str:
        """Renders compact text representation for sliding window context replay."""
        time_str = time.strftime("%H:%M:%S", time.localtime(self.timestamp))
        lines = [
            f"#### [Turn #{self.turn_index}] ({time_str}) - {self.task_type.value}: {self.summary}",
            f"- **사용자 지시**: {self.user_raw_input.strip()}",
        ]
        if self.target_path:
            lines.append(f"- **대상 파일**: `{self.target_path}`")
        if self.commit_hash:
            lines.append(f"- **Git 커밋**: `{self.commit_hash[:7]}` (origin/main 반영 완료)")
        if self.exit_code is not None:
            lines.append(f"- **실행 결과**: Exit Code `{self.exit_code}`")

        if self.pruned_diff:
            lines.append(f"```diff\n{self.pruned_diff}\n```")
        elif self.execution_preview:
            lines.append(f"> {self.execution_preview.strip()}")
        elif self.pruned_stdout:
            lines.append(f"```text\n{self.pruned_stdout}\n```")

        return "\n".join(lines)


@dataclass
class PinnedFacts:
    """Non-degradable facts that bypass LLM summarization."""
    target_repo: str = "gem-bridge"
    touched_files: List[str] = field(default_factory=list)
    last_commit_hash: Optional[str] = None
    user_constraints: Dict[str, str] = field(default_factory=dict)

    def record_touch(self, file_path: Optional[str], commit_hash: Optional[str] = None):
        if file_path and file_path not in self.touched_files:
            self.touched_files.append(file_path)
        if commit_hash:
            self.last_commit_hash = commit_hash

    def to_replay_text(self) -> str:
        files_str = ", ".join([f"`{f}`" for f in self.touched_files[-5:]]) or "없음"
        commit_str = f"`{self.last_commit_hash[:7]}`" if self.last_commit_hash else "없음"
        constraints_str = "; ".join([f"{k}: {v}" for k, v in self.user_constraints.items()]) or "기본 규칙"

        return (
            f"### 🔒 [불변 고정 메타데이터 (Pinned Facts)]\n"
            f"- **대상 저장소**: `{self.target_repo}`\n"
            f"- **최근 수정된 파일 목록**: {files_str}\n"
            f"- **최신 커밋**: {commit_str}\n"
            f"- **고정 규칙**: {constraints_str}\n"
        )


@dataclass
class SessionState:
    """Complete in-memory snapshot of a session."""
    session_id: str
    session_key: str
    target_repo: str
    channel: str = "tasks"
    created_at: float = field(default_factory=time.time)
    last_accessed_at: float = field(default_factory=time.time)
    is_active: bool = True
    rolling_summary: str = ""
    pinned_facts: PinnedFacts = field(default_factory=PinnedFacts)
    turns: List[Turn] = field(default_factory=list)


class SessionPruner:
    """
    Single Responsibility: Trims large diffs and terminal outputs to strict limits.
    Prevents token bloat and context degradation while keeping essential information.
    """

    @staticmethod
    def prune_diff(raw_diff: Optional[str], max_lines: int = 50) -> Optional[str]:
        """Trims unified diff keeping only hunks and changed lines up to max_lines."""
        if not raw_diff or not raw_diff.strip():
            return None

        lines = raw_diff.strip().splitlines()
        if len(lines) <= max_lines:
            return raw_diff.strip()

        pruned: List[str] = []
        for line in lines:
            if line.startswith("@@") or line.startswith("+") or line.startswith("-") or line.startswith("diff --git"):
                pruned.append(line)
            if len(pruned) >= max_lines:
                pruned.append(f"...(남은 {len(lines) - len(pruned)}개 diff 줄 생략)...")
                break

        return "\n".join(pruned)

    @staticmethod
    def prune_stdout(stdout: Optional[str], exit_code: Optional[int] = 0, max_lines: int = 25) -> Optional[str]:
        """Trims terminal output. Returns compact summary for success, traceback tail for failure."""
        if not stdout or not stdout.strip():
            return None

        lines = stdout.strip().splitlines()
        if exit_code == 0:
            # On success, return only the last 3 summary lines (e.g. "15 passed in 0.45s")
            return "\n".join(lines[-3:]) if lines else "✅ 실행 성공"

        # On failure, locate and extract traceback lines or tail
        tb_lines = [l for l in lines if "Error" in l or "Exception" in l or "Traceback" in l or l.startswith("  File")]
        if tb_lines:
            return "\n".join(tb_lines[-max_lines:])
        return "\n".join(lines[-max_lines:])


class SessionStore:
    """
    Single Responsibility: Manages SQLite WAL storage, persistence, and instant recovery.
    """

    DEFAULT_DB_NAME = "sessions.db"

    def __init__(self, storage_dir: Path, ttl_seconds: float = 1800.0):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.storage_dir / self.DEFAULT_DB_NAME
        self.ttl_seconds = float(ttl_seconds)
        self._init_sqlite()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_sqlite(self):
        with self._get_connection() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                session_key TEXT NOT NULL UNIQUE,
                target_repo TEXT NOT NULL,
                channel TEXT NOT NULL,
                created_at REAL NOT NULL,
                last_accessed_at REAL NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                rolling_summary TEXT DEFAULT '',
                pinned_facts_json TEXT DEFAULT '{}'
            );
            """)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS turns (
                turn_id TEXT PRIMARY KEY,
                session_key TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                timestamp REAL NOT NULL,
                task_type TEXT NOT NULL,
                user_raw_input TEXT NOT NULL,
                summary TEXT NOT NULL,
                target_path TEXT,
                commit_hash TEXT,
                exit_code INTEGER,
                execution_preview TEXT,
                pruned_diff TEXT,
                pruned_stdout TEXT,
                FOREIGN KEY (session_key) REFERENCES sessions (session_key) ON DELETE CASCADE
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_key_active ON sessions(session_key, is_active);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_key_index ON turns(session_key, turn_index);")

    def get_or_create_session(self, channel: str, target_repo: str, session_key: str) -> SessionState:
        """Retrieves existing active session or creates a new one."""
        existing = self.load_active_session(session_key)
        if existing:
            return existing

        # Create fresh session
        now = time.time()
        new_id = f"sess_{uuid.uuid4().hex[:8]}"
        pinned = PinnedFacts(target_repo=target_repo)

        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO sessions (
                    session_id, session_key, target_repo, channel, created_at, last_accessed_at,
                    is_active, rolling_summary, pinned_facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, 1, '', ?)
                ON CONFLICT(session_key) DO UPDATE SET
                    session_id = excluded.session_id,
                    target_repo = excluded.target_repo,
                    channel = excluded.channel,
                    created_at = excluded.created_at,
                    last_accessed_at = excluded.last_accessed_at,
                    is_active = 1,
                    rolling_summary = '',
                    pinned_facts_json = excluded.pinned_facts_json
            """, (new_id, session_key, target_repo, channel, now, now, json.dumps(asdict(pinned))))
            # Clear previous turns for this key if re-created
            conn.execute("DELETE FROM turns WHERE session_key = ?", (session_key,))
            conn.commit()

        return SessionState(
            session_id=new_id,
            session_key=session_key,
            target_repo=target_repo,
            channel=channel,
            created_at=now,
            last_accessed_at=now,
            is_active=True,
            rolling_summary="",
            pinned_facts=pinned,
            turns=[]
        )

    def load_active_session(self, session_key: str) -> Optional[SessionState]:
        """Loads active session if not expired by TTL."""
        now = time.time()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT session_id, session_key, target_repo, channel, created_at, last_accessed_at,
                       is_active, rolling_summary, pinned_facts_json
                FROM sessions
                WHERE session_key = ? AND is_active = 1
            """, (session_key,))
            row = cur.fetchone()

            if not row:
                return None

            s_id, s_key, repo, chan, c_at, l_at, active, summary, p_json = row

            # TTL Check
            if (now - l_at) > self.ttl_seconds:
                # Mark expired
                cur.execute("UPDATE sessions SET is_active = 0 WHERE session_key = ?", (session_key,))
                conn.commit()
                return None

            # Load turns
            cur.execute("""
                SELECT turn_id, turn_index, timestamp, task_type, user_raw_input, summary,
                       target_path, commit_hash, exit_code, execution_preview, pruned_diff, pruned_stdout
                FROM turns
                WHERE session_key = ?
                ORDER BY turn_index ASC
            """, (session_key,))

            turns: List[Turn] = []
            for t_row in cur.fetchall():
                turns.append(Turn(
                    turn_id=t_row[0],
                    turn_index=t_row[1],
                    timestamp=t_row[2],
                    task_type=TurnType(t_row[3]),
                    user_raw_input=t_row[4],
                    summary=t_row[5],
                    target_path=t_row[6],
                    commit_hash=t_row[7],
                    exit_code=t_row[8],
                    execution_preview=t_row[9],
                    pruned_diff=t_row[10],
                    pruned_stdout=t_row[11]
                ))

            try:
                pinned = PinnedFacts(**json.loads(p_json))
            except Exception:
                pinned = PinnedFacts(target_repo=repo)

            return SessionState(
                session_id=s_id,
                session_key=s_key,
                target_repo=repo,
                channel=chan,
                created_at=c_at,
                last_accessed_at=l_at,
                is_active=bool(active),
                rolling_summary=summary or "",
                pinned_facts=pinned,
                turns=turns
            )

    def record_turn(self, session_key: str, turn: Turn) -> None:
        """Appends a turn to the session in SQLite and updates last_accessed_at."""
        now = time.time()
        with self._get_connection() as conn:
            cur = conn.cursor()

            # 1. Fetch current pinned_facts
            cur.execute("SELECT pinned_facts_json, target_repo FROM sessions WHERE session_key = ?", (session_key,))
            row = cur.fetchone()
            if row and row[0]:
                try:
                    pinned = PinnedFacts(**json.loads(row[0]))
                except Exception:
                    pinned = PinnedFacts(target_repo=row[1])
            else:
                pinned = PinnedFacts()

            pinned.record_touch(turn.target_path, turn.commit_hash)

            # 2. Insert Turn
            cur.execute("""
                INSERT INTO turns (
                    turn_id, session_key, turn_index, timestamp, task_type, user_raw_input,
                    summary, target_path, commit_hash, exit_code, execution_preview, pruned_diff, pruned_stdout
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                turn.turn_id, session_key, turn.turn_index, turn.timestamp,
                turn.task_type.value, turn.user_raw_input, turn.summary, turn.target_path,
                turn.commit_hash, turn.exit_code, turn.execution_preview, turn.pruned_diff, turn.pruned_stdout
            ))

            # 3. Update session metadata
            cur.execute("""
                UPDATE sessions
                SET last_accessed_at = ?, pinned_facts_json = ?
                WHERE session_key = ?
            """, (now, json.dumps(asdict(pinned)), session_key))

            conn.commit()

    def update_rolling_summary(self, session_key: str, rolling_summary: str) -> None:
        """Updates rolling summary in SQLite."""
        with self._get_connection() as conn:
            conn.execute("UPDATE sessions SET rolling_summary = ? WHERE session_key = ?", (rolling_summary, session_key))
            conn.commit()

    def archive_session(self, session_key: str) -> None:
        """Marks active session as inactive (archived/reset)."""
        with self._get_connection() as conn:
            conn.execute("UPDATE sessions SET is_active = 0 WHERE session_key = ?", (session_key,))
            conn.commit()


class SessionCompactor:
    """
    Single Responsibility: Maintains sliding window of recent turns and folds older turns
    into a rolling summary using Flash-Lite (or deterministic template fallback).
    Enforces strict context token budget.
    """

    def __init__(self, sliding_window_n: int = 2, gemini_client: Optional[object] = None):
        self.sliding_window_n = sliding_window_n
        self.gemini_client = gemini_client

    def compact_if_needed(self, session: SessionState, turns: List[Turn]) -> List[Turn]:
        """
        If turns exceed sliding_window_n, folds the oldest turns into session.rolling_summary
        and returns the retained sliding window turns.
        """
        if len(turns) <= self.sliding_window_n:
            return turns

        turns_to_fold = turns[:-self.sliding_window_n]
        retained_turns = turns[-self.sliding_window_n:]

        self._fold_turns(session, turns_to_fold)
        return retained_turns

    def _fold_turns(self, session: SessionState, turns: List[Turn]) -> None:
        if not turns:
            return

        deltas = []
        for turn in turns:
            delta = f"- [Turn #{turn.turn_index} {turn.task_type.value}] {turn.summary}"
            if turn.target_path:
                delta += f" (파일: `{turn.target_path}`)"
            if turn.commit_hash:
                delta += f" (커밋: `{turn.commit_hash[:7]}`)"
            if turn.exit_code is not None:
                delta += f" (종료코드: `{turn.exit_code}`)"
            deltas.append(delta)

        deltas_str = "\n".join(deltas)

        if not self.gemini_client:
            # Deterministic folding fallback
            if session.rolling_summary:
                session.rolling_summary = f"{session.rolling_summary}\n{deltas_str}".strip()
            else:
                session.rolling_summary = deltas_str.strip()
            return

        try:
            turns_block = "\n".join(
                f"[병합할 턴 #{t.turn_index}]\n"
                f"- 작업 유형: {t.task_type.value}\n"
                f"- 지시: {t.user_raw_input}\n"
                f"- 요약: {t.summary}\n"
                f"- 파일: {t.target_path or 'N/A'}\n"
                f"- 커밋: {t.commit_hash or 'N/A'}\n"
                f"- 종료코드: {t.exit_code if t.exit_code is not None else 'N/A'}"
                for t in turns
            )
            prompt = f"""다음 완료된 개발 턴 목록을 기존 세션 누적 요약(Rolling Summary)에 병합하세요.
코드 전문이나 Diff는 일체 포함하지 말고, 핵심 결정사항과 파일 변경 팩트만 남겨 1,500자 이내로 요약하세요.

[기존 요약]
{session.rolling_summary or '(이전 요약 없음)'}

[추가로 병합할 턴들]
{turns_block}
"""
            resp = self.gemini_client.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents=prompt
            )
            if resp and resp.text:
                session.rolling_summary = resp.text.strip()
            else:
                session.rolling_summary = f"{session.rolling_summary}\n{deltas_str}".strip() if session.rolling_summary else deltas_str.strip()
        except Exception as e:
            logger.warning(f"Flash-Lite consolidation failed: {e}. Using deterministic fallback.")
            session.rolling_summary = f"{session.rolling_summary}\n{deltas_str}".strip() if session.rolling_summary else deltas_str.strip()


class SessionManager:
    """
    Deep Unified Public Seam for gem-bridge v2 session management.
    Orchestrates SessionStore, SessionPruner, and SessionCompactor behind a small interface:
      1. get_or_resume_session(channel, target_repo, incoming_input)
      2. record_turn(session, ...)
      3. build_replay_context(session)
      4. check_git_drift(session, current_git_head)
    """

    RESET_KEYWORDS = ["!새세션", "!리셋", "!reset", "!new", "!새작업", "[새작업]", "!초기화"]

    def __init__(
        self,
        storage_dir: Path,
        ttl_seconds: float = 1800.0,
        sliding_window_n: int = 2,
        gemini_client: Optional[object] = None,
    ):
        self.store = SessionStore(storage_dir=storage_dir, ttl_seconds=ttl_seconds)
        self.pruner = SessionPruner()
        self.compactor = SessionCompactor(sliding_window_n=sliding_window_n, gemini_client=gemini_client)
        self.sliding_window_n = sliding_window_n

    @staticmethod
    def normalize_repo_name(target_repo: str) -> str:
        """Normalizes full git URLs or paths to standard base repo name."""
        if not target_repo:
            return "gem-bridge"
        clean = target_repo.strip().rstrip("/")
        if clean.endswith(".git"):
            clean = clean[:-4]
        parts = re.split(r"[/:]", clean)
        return parts[-1] if parts else clean

    def get_or_resume_session(self, channel: str, target_repo: str, incoming_input: str = "") -> SessionState:
        norm_repo = self.normalize_repo_name(target_repo)
        session_key = f"{channel}:{norm_repo}"
        clean_input = incoming_input.strip()

        # Check explicit reset
        is_reset = any(clean_input.startswith(kw) for kw in self.RESET_KEYWORDS)
        if is_reset:
            self.store.archive_session(session_key)

        session = self.store.get_or_create_session(channel=channel, target_repo=norm_repo, session_key=session_key)
        return session

    def record_turn(
        self,
        session: SessionState,
        task_type: TurnType,
        user_input: str,
        summary: str,
        target_path: Optional[str] = None,
        commit_hash: Optional[str] = None,
        exit_code: Optional[int] = None,
        execution_preview: str = "",
        raw_diff: Optional[str] = None,
        raw_stdout: Optional[str] = None,
    ) -> None:
        pruned_diff = self.pruner.prune_diff(raw_diff)
        pruned_stdout = self.pruner.prune_stdout(raw_stdout, exit_code)

        turn_index = len(session.turns) + 1
        turn = Turn(
            turn_index=turn_index,
            task_type=task_type,
            user_raw_input=user_input,
            summary=summary,
            target_path=target_path,
            commit_hash=commit_hash,
            exit_code=exit_code,
            execution_preview=execution_preview[:300] if execution_preview else "",
            pruned_diff=pruned_diff,
            pruned_stdout=pruned_stdout
        )

        session.turns.append(turn)
        session.pinned_facts.record_touch(target_path, commit_hash)
        self.store.record_turn(session.session_key, turn)

        # Trigger compaction if needed
        retained_turns = self.compactor.compact_if_needed(session, session.turns)
        session.turns = retained_turns
        if session.rolling_summary:
            self.store.update_rolling_summary(session.session_key, session.rolling_summary)

    def build_replay_context(self, session: SessionState) -> str:
        blocks = []
        # 1. Pinned Facts
        blocks.append(session.pinned_facts.to_replay_text())

        # 2. Rolling Summary
        if session.rolling_summary:
            blocks.append(f"### 📜 [누적 작업 히스토리 요약 (Consolidated History)]\n{session.rolling_summary}\n")

        # 3. Recent Sliding Window
        if session.turns:
            blocks.append("### ⚡ [직전 연속 작업 세부 맥락 (Recent Sliding Window)]\n")
            for t in session.turns[-self.sliding_window_n:]:
                blocks.append(t.to_replay_text())
                blocks.append("")

        return "\n".join(blocks).strip()

    def check_git_drift(self, session: SessionState, current_git_head: Optional[str]) -> Optional[str]:
        if not current_git_head:
            return None
        last_commit = session.pinned_facts.last_commit_hash
        if not last_commit:
            return None

        # Compare short hashes (7 chars)
        if not current_git_head.startswith(last_commit[:7]) and not last_commit.startswith(current_git_head[:7]):
            return (
                f"[시스템 동기화 알림] 외부 Git 상태 변경 감지: "
                f"이전 작업 커밋({last_commit[:7]}) -> 현재 워킹 트리 커밋({current_git_head[:7]}). "
                f"로컬 작업 트리가 외부에서 롤백 또는 체크아웃되었습니다. 현재 디스크의 물리적 코드 상태를 기준으로 판단하세요."
            )
        return None

