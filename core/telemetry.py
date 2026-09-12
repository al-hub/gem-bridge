"""
gem-bridge Telemetry & Time Tag Module
Provides precise UTC/KST time formatting, Trace ID generation,
cloud sync lag calculation, and pipeline latency profiling.
"""

from datetime import datetime, timezone, timedelta
import uuid
from typing import Optional

try:
    from zoneinfo import ZoneInfo
    KST = ZoneInfo("Asia/Seoul")
except ImportError:
    KST = timezone(timedelta(hours=9))


class TimeTagFormatter:
    """Utilities for standardized ISO-8601 UTC and KST time formatting."""

    @staticmethod
    def generate_trace_id(prefix: str = "tsk") -> str:
        """Generates a unique sortable trace ID: prefix_YYYYMMDDHHMMSS_xxxx."""
        now_utc = datetime.now(timezone.utc)
        ts_part = now_utc.strftime("%Y%m%d%H%M%S")
        rand_part = uuid.uuid4().hex[:4]
        return f"{prefix}_{ts_part}_{rand_part}"

    @staticmethod
    def format_iso_utc(dt: Optional[datetime] = None) -> str:
        """Formats datetime to ISO 8601 UTC with milliseconds: YYYY-MM-DDTHH:MM:SS.uuuZ."""
        target = dt or datetime.now(timezone.utc)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        else:
            target = target.astimezone(timezone.utc)
        ms = int(target.microsecond / 1000)
        return target.strftime("%Y-%m-%dT%H:%M:%S") + f".{ms:03d}Z"

    @staticmethod
    def format_kst(dt: Optional[datetime] = None, include_seconds: bool = True) -> str:
        """Formats datetime to KST (+09:00): YYYY-MM-DD HH:MM:SS KST."""
        target = dt or datetime.now(timezone.utc)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        kst_dt = target.astimezone(KST)
        fmt = "%Y-%m-%d %H:%M:%S" if include_seconds else "%Y-%m-%d %H:%M"
        return kst_dt.strftime(fmt) + " KST"

    @staticmethod
    def calculate_sync_lag_ms(drive_modified_utc_str: str, received_at: Optional[datetime] = None) -> float:
        """Calculates difference between Drive modifiedTime and received time in milliseconds."""
        if not drive_modified_utc_str:
            return 0.0
        # Parse ISO-8601 string (handles e.g. 2026-09-12T07:19:20.000Z or without fractional seconds)
        clean_str = drive_modified_utc_str.replace("Z", "+00:00")
        try:
            drive_dt = datetime.fromisoformat(clean_str)
        except ValueError:
            return 0.0

        target_received = received_at or datetime.now(timezone.utc)
        if target_received.tzinfo is None:
            target_received = target_received.replace(tzinfo=timezone.utc)

        delta = (target_received - drive_dt).total_seconds() * 1000.0
        return max(0.0, round(delta, 1))


class PipelineProfiler:
    """Measures and reports execution duration for individual pipeline steps."""

    def __init__(self, trace_id: Optional[str] = None):
        self.trace_id = trace_id or TimeTagFormatter.generate_trace_id()
        self.steps = {}
        self.start_perf = datetime.now(timezone.utc)
        self._t0 = None

    class _StepContext:
        def __init__(self, profiler: "PipelineProfiler", name: str):
            self.profiler = profiler
            self.name = name
            self.t0 = 0.0

        def __enter__(self):
            import time
            self.t0 = time.perf_counter()
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            import time
            elapsed = (time.perf_counter() - self.t0) * 1000.0
            self.profiler.steps[self.name] = round(elapsed, 1)

    def step(self, name: str) -> _StepContext:
        """Context manager to measure execution time of a named step."""
        return self._StepContext(self, name)

    def record(self, name: str, duration_ms: float):
        """Directly records a step duration in milliseconds."""
        self.steps[name] = round(duration_ms, 1)

    @property
    def total_duration_ms(self) -> float:
        """Returns sum of measured steps in milliseconds."""
        return sum(self.steps.values())

    def format_summary(self) -> str:
        """Formats a human-readable latency summary string."""
        total_sec = self.total_duration_ms / 1000.0
        step_strs = [f"{k}: {int(v)}ms" for k, v in self.steps.items()]
        return f"{total_sec:.2f}s [" + " | ".join(step_strs) + "]"

    def to_dict(self) -> dict:
        """Serializes profiler metrics into a structured dictionary."""
        return {
            "trace_id": self.trace_id,
            "total_ms": self.total_duration_ms,
            "steps": dict(self.steps)
        }

