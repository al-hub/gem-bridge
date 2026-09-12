"""
gem-bridge CONSOLE Protocol Module
Handles formatting, normalization, and parsing of the single bi-directional
CONSOLE Google Doc used for mobile interaction.
"""

import hashlib
import re
import time
from typing import Dict, List, Optional, Tuple


CONSOLE_DOC_NAME = "[최신결과] CONSOLE"
LEGACY_CONSOLE_DOC_NAME = "CONSOLE"
INPUT_START_TAG = ">>> INPUT >>>"
INPUT_END_TAG = "<<< END <<<"
OUTPUT_SECTION_HEADER = "## 📤 [CONSOLE OUTPUT]"
HISTORY_SECTION_HEADER = "## 📜 [HISTORY]"
DELIMITER_LINE = "══════════════════════════════════════════════════════════════"
DEFAULT_PLACEHOLDER = "(여기에 작업을 입력하세요. 예: !작업 kum 랜딩페이지 개선)"

PLACEHOLDER_PATTERNS = [
    r"^\(여기에\s*작업을?\s*입력하세요.*?\)$",
    r"^\(여기에\s*명령어를?\s*입력하세요.*?\)$",
    r"^\(Write\s+your\s+command\s+here.*?\)$",
    r"^여기에\s*작업을?\s*입력하세요.*$",
]


class ConsoleProtocolParser:
    """Parser and normalizer for the CONSOLE Google Doc content."""

    @staticmethod
    def normalize_mobile_text(text: str) -> str:
        """
        Normalizes dirty text introduced by mobile virtual keyboards,
        including smart quotes, em-dashes, zero-width spaces, and BOMs.
        """
        if not text:
            return ""

        # Remove BOM and zero-width spaces
        cleaned = text.replace("\ufeff", "").replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")

        # Normalize typographic smart quotes
        cleaned = re.sub(r'[\u201c\u201d\u201e\u201f\u00ab\u00bb]', '"', cleaned)
        cleaned = re.sub(r'[\u2018\u2019\u201a\u201b]', "'", cleaned)

        # Normalize typographic dashes
        cleaned = re.sub(r'[\u2013\u2014]', '-', cleaned)

        # Normalize non-breaking spaces
        cleaned = cleaned.replace("\u00a0", " ")

        # Normalize line endings
        cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")

        return cleaned

    @staticmethod
    def is_placeholder(text: str) -> bool:
        """Checks if the extracted command is merely a placeholder or instruction text."""
        trimmed = text.strip()
        if not trimmed:
            return True
        for pattern in PLACEHOLDER_PATTERNS:
            if re.search(pattern, trimmed, re.IGNORECASE):
                return True
        return False

    @classmethod
    def extract_command(cls, raw_text: str) -> Optional[str]:
        """
        Extracts the user command from CONSOLE document raw content.
        Tolerant to missing tags, markdown decoration, and accidental edits.
        """
        if not raw_text:
            return None

        normalized = cls.normalize_mobile_text(raw_text)

        # 1. Try standard >>> INPUT >>> ... <<< END <<<
        pattern_input = re.compile(
            r"(?:\*{0,2}|`{0,3})>>>\s*INPUT\s*>>>(?:\*{0,2}|`{0,3})\s*\n?(.*?)\n?(?:\*{0,2}|`{0,3})<<<\s*END\s*<<<(?:`{0,3}|\*{0,2})",
            re.DOTALL | re.IGNORECASE
        )
        match = pattern_input.search(normalized)
        if match:
            candidate = match.group(1).strip()
            if candidate and not cls.is_placeholder(candidate):
                return candidate
            return None

        # 2. Try [COMMAND] ... [/COMMAND] alternative
        pattern_alt = re.compile(
            r"\[COMMAND\]\s*\n?(.*?)\n?\[/COMMAND\]",
            re.DOTALL | re.IGNORECASE
        )
        match_alt = pattern_alt.search(normalized)
        if match_alt:
            candidate = match_alt.group(1).strip()
            if candidate and not cls.is_placeholder(candidate):
                return candidate
            return None

        # 3. Fallback: Tag opened with >>> INPUT >>> but missing end tag before delimiter or next section header
        pattern_missing_end = re.compile(
            r"(?:\*{0,2}|`{0,3})>>>\s*INPUT\s*>>>(?:\*{0,2}|`{0,3})\s*\n?(.*?)(?=(?:═{5,}|##\s*📜|##\s*\[HISTORY\]|##\s*📤|##\s*\[CONSOLE\s*OUTPUT\]|\Z))",
            re.DOTALL | re.IGNORECASE
        )
        match_missing_end = pattern_missing_end.search(normalized)
        if match_missing_end:
            candidate = match_missing_end.group(1).strip()
            if candidate and not cls.is_placeholder(candidate):
                return candidate
            return None

        # 4. Fallback: Tag erased by user but typed near [명령어 입력창]
        if "명령어 입력창" in normalized:
            after_prompt = normalized.split("명령어 입력창", 1)[1]
            for line in after_prompt.split("\n")[:10]:
                line_s = line.strip().strip(">").strip("=").strip()
                if line_s.startswith("!") or line_s.startswith("{") or any(k in line_s for k in ("gem-bridge", "kum", "tetris-loop")):
                    if not cls.is_placeholder(line_s) and not line_s.startswith("#"):
                        return line_s

        # 5. Ultra-tolerant Fallback: User wiped out tags and typed command at top
        lines = normalized.split("\n")
        non_empty = [l.strip() for l in lines if l.strip()]
        for line in non_empty[:8]:
            if line.startswith("!") or line.startswith("{"):
                if not cls.is_placeholder(line) and not line.startswith("#"):
                    return line

        return None

    @classmethod
    def extract_history(cls, raw_text: str) -> List[str]:
        """Extracts existing history items from the document."""
        if not raw_text:
            return []
        normalized = cls.normalize_mobile_text(raw_text)
        if HISTORY_SECTION_HEADER not in normalized:
            return []
        history_part = normalized.split(HISTORY_SECTION_HEADER, 1)[1].strip()
        items = []
        for line in history_part.split("\n"):
            line = line.strip()
            if line.startswith("- [") or line.startswith("* ["):
                items.append(line)
        return items

    @staticmethod
    def compute_content_hash(text: str) -> str:
        """Computes SHA-256 hash of normalized text for Anti-Echo Layer 1."""
        normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def compute_command_hash(cmd: str) -> str:
        """Computes SHA-256 hash of normalized command for Anti-Echo Layer 2."""
        normalized = re.sub(r"\s+", " ", (cmd or "").strip())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class ConsoleDocFormatter:
    """Renders formatted CONSOLE Google Doc content."""

    @staticmethod
    def make_status_badge(status: str, timestamp_str: Optional[str] = None) -> str:
        """Generates visual status badge for mobile screen."""
        ts = timestamp_str or time.strftime("%Y-%m-%d %H:%M:%S")
        status_upper = status.upper()
        if status_upper == "ONLINE":
            return f"🟢 ONLINE (통신: {ts} KST)"
        elif status_upper == "PROCESSING":
            return f"🔄 PROCESSING (시작: {ts} KST)"
        elif status_upper == "OFFLINE":
            return f"🔴 OFFLINE (PC 전원 꺼짐: {ts} KST)"
        elif status_upper == "ERROR":
            return f"⚠️ ERROR (오류: {ts} KST)"
        else:
            return f"⚪ {status} ({ts} KST)"

    @staticmethod
    def make_action_banner(action_status: Optional[str], action_message: Optional[str] = None) -> str:
        """Generates high-contrast action status banner for CONSOLE output."""
        if not action_status:
            return ""
        status_upper = action_status.upper()
        if status_upper == "COMMIT_SUCCESS":
            header = "🟢 **[작업 완료 / Git 반영]**"
        elif status_upper == "GUARDRAIL_REDIRECT":
            header = "🟡 **[가드레일 작동 / 분석 대체]**"
        elif status_upper == "ERROR":
            header = "🔴 **[작업 실패 / 오류]**"
        elif status_upper == "READ_SUCCESS":
            header = "🔵 **[조회/분석 완료]**"
        else:
            header = f"ℹ️ **[{action_status}]**"

        if action_message:
            return f"> {header}\n> {action_message}\n\n"
        return f"> {header}\n\n"

    @classmethod
    def render(
        cls,
        status: str,
        input_command: Optional[str] = None,
        output_content: Optional[str] = None,
        history_items: Optional[List[str]] = None,
        timestamp_str: Optional[str] = None,
        trace_id: Optional[str] = None,
        duration_summary: Optional[str] = None,
        sync_lag_ms: Optional[float] = None,
        action_status: Optional[str] = None,
        action_message: Optional[str] = None,
    ) -> str:
        """
        Renders the complete markdown text for CONSOLE Google Doc.
        Top-Anchored layout for mobile: Output & Action Banner at the very top (Above-the-Fold)
        so Gemini Workspace tool reads the latest result on the first snippet window.
        """
        badge = cls.make_status_badge(status, timestamp_str)
        cmd_text = (input_command or DEFAULT_PLACEHOLDER).strip()
        out_text = (output_content or "*(아직 실행된 결과가 없습니다. 아래 입력창에 작업을 입력하세요.)*").strip()

        # Action banner
        action_banner = cls.make_action_banner(action_status, action_message)

        # Telemetry meta bar
        telemetry_lines = []
        if trace_id:
            telemetry_lines.append(f"**Trace ID**: `{trace_id}`")
        if duration_summary:
            telemetry_lines.append(f"⏱️ **소요 시간**: `{duration_summary}`")
        if sync_lag_ms is not None:
            telemetry_lines.append(f"📡 **동기화 지연**: `{int(sync_lag_ms)}ms`")

        meta_bar = ""
        if telemetry_lines:
            meta_bar = "> " + " | ".join(telemetry_lines) + "\n\n"

        history_list = history_items or []
        if history_list:
            history_text = "\n".join(history_list[:3])
        else:
            history_text = "*(최근 실행 이력이 없습니다.)*"

        offline_notice = ""
        if status.upper() == "OFFLINE":
            offline_notice = "> 💡 **안내**: 현재 PC가 꺼져 있습니다. 입력창에 작업을 적어두시면 PC가 켜질 때 자동으로 실행됩니다.\n\n"

        # Top-Anchored Layout: Output at the top, Input below
        doc_content = f"""# 📱 gem-bridge CONSOLE        [{badge}]
{DELIMITER_LINE}
{OUTPUT_SECTION_HEADER}
{action_banner}{meta_bar}{out_text}

{DELIMITER_LINE}
{offline_notice}▼ **[명령어 입력창]** (수정 후 1~3초 내 자동 실행됩니다)
{INPUT_START_TAG}
{cmd_text}
{INPUT_END_TAG}
{DELIMITER_LINE}

{HISTORY_SECTION_HEADER} (최근 3개)
{history_text}
"""
        return doc_content
