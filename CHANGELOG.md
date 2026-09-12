# Changelog

All notable changes to `gem-bridge` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> ⚠️ **Versioning Policy**:
> - **Major Version Lock**: Major version (`v2.x.x` -> `v3.0.0`) is strictly frozen and will **NEVER** be updated without explicit user permission.
> - **Permitted Increments**: Only Minor / Patch updates (`v2.x.y`) are performed.

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
