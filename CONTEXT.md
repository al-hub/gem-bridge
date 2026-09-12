# Domain Glossary: gem-bridge

## Terms

### TaskChannel
A bidirectional communication pathway connecting an external client (such as Google Tasks, Google Docs CONSOLE, or Drive ephemeral documents) to the local execution daemon.

### PendingTask
An unexecuted instruction ingested from a `TaskChannel` that requires intent analysis, repository preparation, and execution (READ, WRITE, or EXEC).

### TaskFeedback
The structured execution outcome (summary, file manifest, commit hash, git diff, or console output) delivered back across a `TaskChannel` to inform the user.

### Anti-Reexecution Guard
A multi-layered defensive filter across an ingestion seam ensuring that an active, completed, or in-flight task is never redundantly executed by polling loops.

### TaskLifecycle
The state transition rules governing a task across its existence: ingestion (`needsAction`) → in-flight → feedback published (`needsAction` with `[✅완료]` prefix) → archival (`completed` after 24 hours).

### TasksLightMode
An optimized execution mode where continuous Drive API polling (CONSOLE modifiedTime checks, candidate document scans, heartbeat updates) is silenced, directing all polling cycles to Google Tasks for near-zero latency (<0.1s idle) while preserving optional one-shot Drive report backups.

### ZeroClickTitle
A high-density feedback format embedded directly into the task title (e.g. `[✅완료: f163137] docs/guide.md - 에러 핸들링 보강`) providing instantaneous outcome visibility in mobile Gemini card widgets without requiring the user to tap external application links.
