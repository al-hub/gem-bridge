import unittest
from datetime import datetime, timezone
from core.telemetry import TimeTagFormatter


class TestTimeTagFormatter(unittest.TestCase):

    def test_generate_trace_id_format(self):
        trace_id = TimeTagFormatter.generate_trace_id()
        # Expect format: tsk_YYYYMMDD_HHMMSS_xxxx
        self.assertTrue(trace_id.startswith("tsk_"))
        parts = trace_id.split("_")
        self.assertEqual(len(parts), 3)
        self.assertEqual(len(parts[1]), 14)  # YYYYMMDDHHMMSS
        self.assertEqual(len(parts[2]), 4)   # 4 hex chars

    def test_format_iso_utc_includes_millis_and_z(self):
        fixed_dt = datetime(2026, 9, 12, 7, 19, 23, 456000, tzinfo=timezone.utc)
        iso_str = TimeTagFormatter.format_iso_utc(fixed_dt)
        self.assertEqual(iso_str, "2026-09-12T07:19:23.456Z")

    def test_format_kst_converts_utc_to_kst_accurately(self):
        # 07:19:23 UTC should be 16:19:23 KST (+9 hours)
        fixed_utc = datetime(2026, 9, 12, 7, 19, 23, 456000, tzinfo=timezone.utc)
        kst_str = TimeTagFormatter.format_kst(fixed_utc)
        self.assertEqual(kst_str, "2026-09-12 16:19:23 KST")

    def test_calculate_sync_lag_ms(self):
        # Drive modifiedTime: 07:19:20.000Z
        # Received at: 07:19:21.500Z
        # Expected lag: 1500 ms
        drive_time = "2026-09-12T07:19:20.000Z"
        received_at = datetime(2026, 9, 12, 7, 19, 21, 500000, tzinfo=timezone.utc)
        lag_ms = TimeTagFormatter.calculate_sync_lag_ms(drive_time, received_at)
        self.assertEqual(lag_ms, 1500.0)


class TestPipelineProfiler(unittest.TestCase):

    def test_profiler_steps_and_summary(self):
        from core.telemetry import PipelineProfiler
        import time

        profiler = PipelineProfiler(trace_id="tsk_test_001")
        with profiler.step("drive_export"):
            time.sleep(0.01)  # ~10ms
        with profiler.step("intent_llm"):
            time.sleep(0.015) # ~15ms

        self.assertEqual(profiler.trace_id, "tsk_test_001")
        self.assertIn("drive_export", profiler.steps)
        self.assertIn("intent_llm", profiler.steps)
        self.assertGreaterEqual(profiler.steps["drive_export"], 8.0)
        self.assertGreaterEqual(profiler.steps["intent_llm"], 12.0)
        self.assertGreaterEqual(profiler.total_duration_ms, 20.0)

        summary = profiler.format_summary()
        self.assertIn("drive_export:", summary)
        self.assertIn("intent_llm:", summary)

        data = profiler.to_dict()
        self.assertEqual(data["trace_id"], "tsk_test_001")
        self.assertIn("steps", data)
        self.assertIn("total_ms", data)


if __name__ == "__main__":
    unittest.main()
