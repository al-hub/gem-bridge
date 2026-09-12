# Changelog

All notable changes to `gem-bridge` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> ⚠️ **Versioning Policy**:
> - **Locked to v2.1.x**: Major(1st) and Minor(2nd) digits are strictly frozen without explicit user permission.
> - **Permitted Increments**: Only the 3rd position `z` (`v2.1.z`) is updated (`v2.1.1`, `v2.1.2`, ...).

---

## [2.1.4] - 2026-09-12

### Added
- **Bidirectional Task Completion Sync to CONSOLE (`daemon_v2.py`)**:
  - Added `_sync_task_result_to_console` and `_read_console_content` to `DaemonV2`.
  - All task types (`READ`, `WRITE`, `EXEC`) submitted via Google Docs task documents (`!작업`, `!분석`, `!실행`) now immediately synchronize their execution status, action banners (`COMMIT_SUCCESS`, `READ_SUCCESS`), summaries, and diffs to the `CONSOLE` Google Doc.
  - Ensures mobile Gemini Gem always has instant access to the latest task results when the user asks "결과 알려줘", completely eliminating stale console outputs.

## [2.1.3] - 2026-09-12

### Added
- **Natural Language Refactoring & Code Synthesis Engine (`core/executor_write.py`, `core/intent_analyzer.py`)**:
  - `IntentAnalysisResult` extended with `source_path` and `instruction` fields.
  - Guardrail adjusted: natural language modification/move requests no longer get unconditionally downgraded to `READ` when full file `content` is missing from the mobile command.
  - Autonomous Code Synthesis (`WriteExecutor.synthesize_code`): uses `gemini-3.6-flash` to read baseline files, interpret natural language instructions (e.g. adjust relative links, fix functions), and synthesize target code.
  - File Move & Rename Automation: handles `source_path` ➔ `target_path` transition, removes original file, and stages git operations (`git add -u`) for seamless commit & push.
- **Action Status Banner System (`core/console_protocol.py`)**:
  - High-contrast visual action banners embedded in the `GeminiBridge/CONSOLE` document output section:
    - 🟢 `[작업 완료 / Git 반영]` (`COMMIT_SUCCESS`)
    - 🟡 `[가드레일 작동 / 분석 대체]` (`GUARDRAIL_REDIRECT`)
    - 🔴 `[작업 실패 / 오류]` (`ERROR`)
    - 🔵 `[조회/분석 완료]` (`READ_SUCCESS`)
  - Solves user status blindness by immediately communicating whether an operation was committed, redirected, or encountered an error.
- **Test Suite Expansion**:
  - 45 unit tests passing across protocol, intent analyzer, write executor, read executor, telemetry, and daemon.

---

## [2.1.2] - 2026-09-12

### Verified & Hardened
- **Reboot Auto-Startup & Unattended Cold-Boot**:
  - Validated headless 2-stage launch via Windows Startup (`start_wsl_bridge.vbs`) and WSL systemd service (`gem-bridge.service` enabled).
- **PC Offline Perception & Auto-Queueing**:
  - Graceful shutdown hook (`SIGTERM` trap) verified to immediately publish `[🔴 OFFLINE]` badge to `GeminiBridge/CONSOLE`.
  - Offline command auto-queueing verified: instructions drafted on mobile while PC is off execute automatically upon system boot.
  - Zero-drift KST/UTC time tag comparison verified for ungraceful outage detection.

---

## [2.1.1] - 2026-09-12

### Added
- **Pipeline Telemetry & Latency Profiling System (`core/telemetry.py`)**:
  - `PipelineProfiler`: Step-level context timer measuring execution time (ms) across Drive export, LLM intent analysis, Git operations, and writeback.
  - `TimeTagFormatter`: Microsecond/millisecond UTC ISO-8601 formatting, strict `ZoneInfo("Asia/Seoul")` KST conversion, and Drive-WSL cloud sync lag calculation (`sync_lag_ms`).
  - Unique `Trace ID` generation (`tsk_YYYYMMDD_HHMMSS_xxxx`) injected across logs, `GeminiBridge/CONSOLE` doc, and `GeminiBridge/STATUS` doc for 100% end-to-end task auditability.
- **TDD Test Suite Expansion**:
  - Added `tests/test_telemetry.py` (5 tests covering trace IDs, UTC/KST conversions, profiler steps, and sync lag calculation).
  - Added telemetry assertions to `tests/test_daemon_v2.py` and `tests/test_console_protocol.py` (total 41 tests passing 100%).

---

## [2.1.0] - 2026-09-12

### Added
- **Single Bi-Directional Mobile `CONSOLE` Document (`GeminiBridge/CONSOLE`)**:
  - Direct interaction hub optimized for smartphone Google Docs & Google Gemini mobile.
  - Top-anchored input area (`>>> INPUT >>> ... <<< END <<<`) immune to virtual keyboard occlusion.
  - Fault-tolerant mobile preprocessor handling typographic quotes, em-dashes, BOMs, and zero-width spaces.
  - Automatic Rolling History (sliding window N=3) preventing document bloat.
- **3-Layer Anti-Echo / Self-Loop Defense**:
  - Layer 1: SHA-256 caching of daemon-written document text.
  - Layer 2: SHA-256 hash tracking of normalized user commands.
  - Layer 3: Google Drive `modifiedTime` metadata pre-filtering.
- **PC Offline Detection & Heartbeat**:
  - Graceful shutdown signal traps (`SIGTERM`, `SIGINT`) instantly setting `[🔴 OFFLINE]` badge.
  - Heartbeat timestamp tracking for ungraceful power outage/sleep detection.
  - Unattended auto-queueing: commands typed while PC is off execute automatically upon PC boot.
- **100% Backward Compatibility**:
  - Preserved ephemeral task document ingestion and `STATUS` doc tracking.

---

## [2.0.1] - 2026-09-12

### Added
- **Windows-WSL Unattended 2-Stage Auto-Startup**:
  - Added `start_wsl_bridge.vbs` to Windows Startup (`shell:startup`) for headless WSL background cold-booting.
  - Added comprehensive documentation: `docs/AUTO_STARTUP_GUIDE.md`.
- **Formal Semantic Versioning**:
  - Added `core/__version__.py` (`__version__ = "2.0.1"`) as Single Source of Truth.
  - Added `VERSION` file.
  - Added `CHANGELOG.md` with strict versioning policy (Major version locked).
  - Integrated dynamic versioning in `daemon_v2.py` startup logging.

---

## [2.0.0] - 2026-09-12

### Added
- **Modular Dispatcher-Executor Architecture**:
  - Replaced legacy monolithic `bridge_daemon.py` with modular agent pipeline.
- **`core/intent_analyzer.py`**:
  - Natural language instruction and explicit command (`!분석`, `!작업`, `!실행`) parsing.
  - Pydantic structured JSON schema enforcement with `gemini-3.6-flash`.
  - **Default=READ Guardrail**: Unclear requests or missing write details automatically downgrade to `READ`.
- **`core/repo_manager.py`**:
  - Public Git URL detection and shallow clone/pull (`--depth 1`) under `~/workspace/repos/`.
  - Local repository permission and existence validation against `config.json`.
- **`core/executor_read.py` (READ-Only)**:
  - Repository context gathering (directory tree + source snippets).
  - Markdown report generation via `gemini-3.6-flash`.
  - Automatic Google Doc creation (`[보고서] ...`) via Google Drive API.
  - Strict isolation: Git Push is physically forbidden.
- **`core/executor_write.py` (WRITE-Only)**:
  - Protected file overwrite prevention (`README*`, `ARCHITECTURE*`, `credentials.json`, `token.json`, `.env*`).
  - Unified Diff generation and audit logging before write.
  - Automated `git add`, `git commit -m`, and `git push`.
- **`core/executor_exec.py` (EXEC-Only)**:
  - Guarded shell command execution with dangerous pattern filtering.
  - Results exported to Google Docs (`[실행결과] ...`).
- **`daemon_v2.py`**:
  - Google Drive polling dispatcher with robust crash prevention.
  - Processed documents automatically moved to trash.
  - Errors safely reported to Google Drive (`[오류] ...`) and `result.log` without daemon termination.
- **Test Suite**:
  - Comprehensive unit and integration test suite (`tests/`).

---

## [1.0.0] - 2026-09-12

### Initial
- Monolithic prototype `bridge_daemon.py` connecting Google Drive to local repositories.
