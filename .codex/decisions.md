# Decision Log

## Accepted

### D-001: Logs are staged before final rendering

All log entries are written to `10 - Logs/00 - Inbox` first. Final daily logs under `06 - Timestamps` are rendered later from staged entries and ledger state.

Reason: This preserves recovery paths and prevents duplicate or partial final daily logs.

### D-002: SQLite ledger is the source of truth

Use SQLite to track recordings, checksums, job states, transcript paths, Obsidian paths, consolidation status, and rebuild history.

Reason: Idempotency, recovery, and late-arrival behavior need transactional state outside Markdown files.

### D-003: OpenClaw receives a narrow HTTP API

OpenClaw may observe status and request bounded actions, but it must not run arbitrary shell commands, delete files directly, or rewrite final logs outside the service API.

Reason: The PRD explicitly calls out OpenClaw overreach as a risk.

### D-004: Diarization model is configurable

Use the PRD's `pyannote/speaker-diarization-3.1` as the baseline requirement, but keep the model configurable and evaluate `pyannote/speaker-diarization-community-1` before finalizing the default.

Reason: Research shows `community-1` is a newer open-source pipeline with improved speaker assignment/counting, while the PRD still names 3.1.

### D-005: Host communication uses direct internal APIs

Use direct LAN or loopback HTTP APIs between the recorder host, `odin`, and OpenClaw. Keep `fenrir` out of the transcription path, keep `qnap` out of new dependencies, and use `saga` only for backups/archive unless explicitly re-decided.

Reason: `prager.ws` identifies `fenrir` as the public edge, `odin` as the internal service/observability host, `saga` as storage, `qnap` as legacy, and `mimir` as OpenClaw host. Direct scoped APIs minimize hidden runtime dependencies and match the PRD's bounded-action security model.

### D-006: `mimir` is the Mac Mini recorder host

Run the recorder-side Logbook service on `mimir`, where OpenClaw also runs as `clawdbot`.

Reason: The user confirmed the PRD's Mac Mini is `mimir`.

### D-007: Use scoped bearer tokens for MVP auth

Use separate scoped bearer tokens for `mimir` to `odin` GPU jobs, Logbook read access, and Logbook action access. Keep tokens in `.env` and bind the OpenClaw-facing Logbook API to loopback.

Reason: This is operationally simpler than mTLS, appropriate for loopback/internal LAN services, and keeps privileges narrow. Revisit mTLS if the API crosses a less-trusted boundary.

### D-008: Use Obsidian CLI against GitHub-backed vault

Use the Obsidian CLI to access and update the vault at `https://github.com/bprager/obs-vault.git`.

Reason: The user identified the vault source and requested Obsidian CLI access/update behavior.

### D-009: Delete source audio after 24 hours

Do not link source audio from Obsidian. Delete local source audio and recorder-side audio after 24 hours, after processing, vault write, and sync are confirmed.

Reason: The user explicitly requested deletion after 24 hours, including on the Sony voice recorder. This supersedes the PRD MVP non-goal about avoiding automatic recorder deletion.

### D-010: Audio retention age is based on the ingestion ledger

Use SQLite ledger timestamps such as `copied_at`, `processed_at`, `vault_synced_at`, and `cleanup_eligible_at` to decide when audio may be deleted. Do not use recorder filenames or recorder filesystem modification times for retention eligibility.

Reason: The recorder clock can drift or be corrected after files already exist. Using ledger timestamps prevents filename or filesystem metadata from making fresh files look eligible for cleanup too early.

### D-011: Obsidian CLI commands are configured templates

Keep the vault workflow bound to Obsidian CLI command templates in `.env` instead of hard-coding unconfirmed subcommands.

Reason: `obsidian` is not currently discoverable on `PATH` on this host, and the exact sync/status/commit/push syntax still needs confirmation. Templates let the code enforce preflight, locking, and command ordering without guessing the installed CLI's interface.

### D-012: Obsidian note writes can use `obsidian-cli create`

Support `obsidian-cli create <note> --vault <vault-name> --content <markdown> --overwrite` as an optional writer for routed notes.

Reason: The installed `obsidian-cli` v0.2.3 supports note create/list operations, but it relies on Obsidian's vault registry and URI handling. Direct filesystem writes remain the test/scratch fallback until the target vault is opened or registered in Obsidian.

### D-013: StartOnMount runs bounded processing, not discovery only

The recorder mount LaunchAgent runs `logbook process-mounted-recorder --env .env`, which copies new files, submits transcription, routes generated notes, marks pushed vault artifacts as synced, and refreshes Memgraph evidence. It remains bounded and recoverable, and it never deletes recorder or local source audio.

Reason: A read-only mount probe can prove that a recorder mounted without advancing the ledger or vault. The May 5 meeting incident showed that mount-triggered automation must execute the same recoverable ingest path an operator would run manually.

### D-014: Vault sync isolates Obsidian workspace state

The generated-note vault workflow may preserve or ignore `.obsidian/workspace.json`, but that local UI state must not block generated Logbook note commits. Generated-note roots are ensured before staging so command templates do not fail on missing directories.

Reason: Obsidian workspace state is operator-local UI state, while Logbook notes are recoverable generated artifacts. A workspace conflict must not prevent processed meetings, logs, notes, or dead letters from reaching the vault.

## Open Questions

1. Should the current open day have an Obsidian preview note?
2. Should consolidated inbox entries remain in the vault forever or be archived outside the vault?
3. Which category prefixes beyond `idea`, `task`, `research`, `reminder`, and `meeting` should be preconfigured?
4. Should late arrivals notify OpenClaw instead of silently rebuilding?
5. Should meeting summaries be generated automatically or only after manual review?
6. Which exact Obsidian CLI command templates should be filled into `.env` for sync/status/commit/push on this host?
7. What is the exact Sony ICD-PX370 mounted volume path/name on `mimir`?
8. Should 24-hour local cleanup move audio to trash/quarantine first, or hard-delete immediately after confirmed sync?
9. Should the one-minute timestamp mismatch on `260427_1351.mp3` be treated as expected recorder behavior or corrected manually?
