# Changelog

All notable changes to `gem-bridge` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> ⚠️ **Versioning Policy**:
> - **Locked to v2.1.x**: Major(1st) and Minor(2nd) digits are strictly frozen without explicit user permission.
> - **Permitted Increments**: Only the 3rd position `z` (`v2.1.z`) is updated (`v2.1.1`, `v2.1.2`, ...).

---

## [2.1.10] - 2026-09-12

### Added
- **Deepened Google Tasks Autonomous Feedback Channel (`core/google_tasks.py`, `daemon_v2.py`)**:
  - Implemented `update_task_with_feedback()` retaining `status='needsAction'` while updating titles with `[✅완료]` or `[❌오류]`, allowing mobile Gemini to immediately query and brief completed tasks without requiring `showCompleted=True`.
  - Added Anti-Reexecution Double Guard: `list_pending_tasks()` automatically ignores tasks starting with `("[✅완료]", "[❌오류]", "[⏳진행]")`, backed by in-memory `processed_task_ids`.
  - Rich Plain-Text Notes formatting: structured output (no confusing raw markdown fences) capped at 8,000 characters for READ (architecture, file previews, Google Drive report link), WRITE (commit hash, target file, diff snippet), and EXEC (exit code, console stdout/stderr).
  - Robust per-task exception handling ensuring errors result in actionable `[❌오류]` diagnosis in Google Tasks.
  - Automated Task Lifecycle Management: `archive_stale_tasks()` automatically transitions completed/error tasks older than 24 hours to `completed` status during the 6-hour periodic janitor cycle.
  - Expanded unit test suite to 73 tests covering prefix filtering, rich feedback updates, stale archiving, and daemon task processing (100% passing).

---

## [2.1.9] - 2026-09-12

### Changed
- **High-Speed Google Docs Export Optimization (`daemon_v2.py`)**:
  - Replaced blocking `export_media()` calls with fast native `files().export()` across all CONSOLE read/heartbeat operations.
  - Reduced CONSOLE polling export latency from ~60s socket read timeout down to ~1.0s, eliminating httplib2 timeout warnings.
  - Implemented robust fallback to `export_media()` for complete test mock compatibility.
- **End-to-End Verified 0-Tap `@Google Tasks` Autonomous Loop**:
  - Successfully verified real-world Google Tasks ingestion, shallow cloning of private/public GitHub repositories (`al-hub/remote_codex`), automated LLM architecture analysis, report generation on Google Drive, and automated completion status sync.

---

## [2.1.8] - 2026-09-12

### Added
- **0-Tap Voice/Text Mobile Automation via `@Google Tasks` (`core/google_tasks.py`)**:
  - Implemented `GoogleTasksManager` integrating Google Tasks API (`tasks.googleapis.com/tasks/v1`) with combined OAuth scopes (`drive` + `tasks`).
  - Seamless 0-Tap mobile UX: Users speak or type naturally in mobile Gemini (e.g. `"@Google Tasks gem-bridge docs/guide.md 수정하고 푸시해줘 등록해줘"`), and Gemini creates the task without clicking any buttons.
  - PC Daemon (`daemon_v2.py`) automatically polls `@default` Google Tasks, analyzes pure natural language via `IntentAnalyzer`, executes `WriteExecutor` (Git commit & push), `ReadExecutor`, or `ExecExecutor`, marks the task as completed, and appends completion notes with commit hashes.
  - Automatic sync to `[최신결과] CONSOLE` and `STATUS` documents for in-chat feedback.
- **Combined OAuth Authorization Wizard (`auth_helper.py`)**:
  - Updated OAuth scopes to include both `https://www.googleapis.com/auth/drive` and `https://www.googleapis.com/auth/tasks`.
  - Provided interactive setup wizard for generating combined `token.json`.
- **Test Suite Expansion (`tests/test_google_tasks.py`, `tests/test_daemon_v2.py`)**:
  - Added 6 new unit tests for Google Tasks listing, completion, error handling, and daemon integration (67 total tests passing).

---

## [2.1.7] - 2026-09-12

### Added
- **Gemini Mobile Native 1-Tap & 1-Click Zero-Copy Dispatcher (`daemon_v2.py`)**:
  - Full native support for Google Gemini mobile app's built-in **[공유] ➔ [Google 문서로 내보내기] (Export to Docs)** action (`Gemini - *` documents).
  - Automatically detects exported Gemini task documents, extracts intent, dispatches to `ReadExecutor`/`WriteExecutor`, trashes the temporary document, and syncs output to `[최신결과] CONSOLE`.
  - Added `"gemini"` keyword to `TRIGGER_KEYWORDS` and prefix matching for `Gemini -` exports.
  - Zero-Copy & Zero-Paste: Users never need to manually copy or paste text between apps; single tap on Google's native export button dispatches the task immediately.
- **Protocol & System Instructions Update (`docs/MOBILE_GEM_PROTOCOL.md`)**:
  - Documented both Native Export 1-Tap and 1-Click Webhook execution paths.
  - In-Chat 0-Click verification via Gemini's native `@Google Drive` extension.

---

## [2.1.6] - 2026-09-12

### Added
- **Gemini Mobile All-in-One Deterministic Protocol (`docs/MOBILE_GEM_PROTOCOL.md`)**:
  - Full production-ready system instructions for custom Google Gemini Gem to prevent Tool Call Drops and eliminate Zombie Loops.
  - Zero-Token Output Gate: Prohibits text emission prior to actual Google Workspace tool invocation.
  - Fast-Fail FSM: Immediate failure response (`⚠️ [등록된 작업 없음]`) on "결과 알려줘" when CONSOLE is idle, cutting zombie loops at turn 1.
  - In-Chat Diff Viewer: Delivers formatted code diffs directly within the Gemini mobile chat thread without external app navigation.
- **Single Source of Truth (SSOT) Anchor & Top-Anchored Layout (`core/console_protocol.py`)**:
  - Renamed singleton document to `[최신결과] CONSOLE` for instant BM25 lexical keyword matching during Gemini mobile search queries (`"결과 알려줘"`, `"최신 상태"`).
  - Above-the-Fold Optimization: Reordered layout to place `## 📤 [CONSOLE OUTPUT]` and action banners at the very top of the document so mobile Gemini parses results on the first 1KB snippet.
- **Adaptive Dual-Rate Polling & Fast-Path Routing (`daemon_v2.py`)**:
  - Active burst polling at 1.0s interval during user interaction (< 5min of activity), reducing end-to-end command latency to 2~3s.
  - Fast-Path Regex routing: Directly executes `!실행 <cmd>`, `!분석 <repo> <query>`, and janitor commands in 0ms without Gemini LLM roundtrip.
  - Non-Destructive Overwrite Guard: Prevents silent data loss by verifying if a user typed a new command during task execution before resetting the input area.
  - Boot-time crash auto-healing: Automatically recovers zombie `PROCESSING` states to `ONLINE` on daemon restart.
- **Test Suite Expansion**:
  - 61 unit tests passing across all components, including new tests for adaptive polling, non-destructive guard, fast-path routing, and top-anchored layout.

## [2.1.5] - 2026-09-12

### Added
- **Hierarchical Drive Storage & Mobile-First Naming (`core/drive_storage.py`)**:
  - `DriveStorageManager`: Manages nested category folders under `GeminiBridge/` (`reports/YYYY-MM`, `commits/YYYY-MM`, `logs/YYYY-MM`, `logs/errors`, `archive/`).
  - Mobile-First Naming: Enforces `[{TYPE_ICON}{TYPE_TAG}:{REPO_SHORT}] {SUMMARY} ({YYMMDD_HHmm})` format (e.g. `[📄분석:bridge] 상세 개발이력 분석 (260912_1725)`), preventing title truncation on mobile screens.
  - In-memory ID caching for fast resolution without redundant Google Drive API calls.
- **Storage Lifecycle & Auto-Purge Janitor Engine (`core/janitor.py`)**:
  - `StorageJanitor`: Implements tiered document retention (Tier 1: `logs/` 3 days, Tier 2: `commits/` 7 days, Tier 3: `reports/` 30 days, Tier 0: `CONSOLE`/`STATUS` permanent singletons).
  - 2-Stage Hard-Purge: Permanently purges Google Drive trash (`files.delete`) to eliminate Gemini AI RAG citation confusion and save storage space.
  - Interactive Janitor Commands: Supports `clean logs`, `clean trash`, `storage stats`, `드라이브 정리` directly from mobile Gem / CONSOLE.
  - Automatic scheduled background cleanup in daemon every 6 hours.
- **Test Suite Expansion**:
  - 57 unit tests passing across all components (`test_drive_storage.py`, `test_janitor.py`, `test_daemon_v2.py`, etc.).

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
