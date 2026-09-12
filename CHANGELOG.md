# Changelog

All notable changes to `gem-bridge` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> ⚠️ **Versioning Policy**:
> - **Major Version Lock**: Major version (`v2.x.x` -> `v3.0.0`) is strictly frozen and will **NEVER** be updated without explicit user permission.
> - **Permitted Increments**: Only Minor / Patch updates (`v2.x.y`) are performed.

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
