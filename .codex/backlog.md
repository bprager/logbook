# Backlog

Updated: 2026-04-27

Project key in memgraph: `logbook`

## Dependency Map

```text
LGB-001 Foundations
  -> LGB-002 Configuration
    -> LGB-003 SQLite ledger
    -> LGB-004 Path builders
    -> LGB-005 Mount probe
    -> LGB-007 Odin job contract

LGB-005 + LGB-003 -> LGB-006 Copy and dedupe ingest
LGB-007 -> LGB-008 ASR worker -> LGB-009 Transcript persistence -> LGB-010 Prefix classifier
LGB-004 -> LGB-011 Markdown renderers
LGB-002 -> LGB-025 Obsidian CLI vault workflow
LGB-010 + LGB-011 + LGB-025 -> LGB-012 Log inbox writer
LGB-010 + LGB-011 + LGB-025 -> LGB-013 Category note writer
LGB-010 + LGB-011 + LGB-025 -> LGB-014 Dead-letter writer
LGB-012 + LGB-003 + LGB-004 + LGB-025 -> LGB-015 Consolidation engine -> LGB-016 Late-arrival rebuild
LGB-010 + LGB-007 -> LGB-017 Meeting diarization
LGB-017 + LGB-011 + LGB-025 -> LGB-018 Meeting note renderer
LGB-003 + LGB-012 + LGB-014 + LGB-015 -> LGB-019 Status API -> LGB-020 Bounded action API
LGB-006 + LGB-019 -> LGB-021 launchd packaging
LGB-003 + LGB-006 + LGB-009 + LGB-012 + LGB-013 + LGB-014 + LGB-018 + LGB-025 -> LGB-026 Audio retention cleanup
LGB-012 + LGB-013 + LGB-014 + LGB-016 + LGB-018 + LGB-020 + LGB-025 + LGB-026 -> LGB-022 End-to-end acceptance tests
```

## Milestone 0: Repo And Product Foundations

### LGB-001 - Foundations

Status: Ready

Dependencies: none

Deliverables:

- Confirm implementation language and package layout.
- Define local development commands.
- Add baseline test and lint tooling.
- Keep generated data and secrets out of git.

Acceptance:

- A new Codex session can find context, decisions, backlog, and PRD quickly.
- Repo has clear instructions for safe local-first development.

### LGB-002 - Configuration

Status: Ready after LGB-001

Dependencies: LGB-001

Deliverables:

- Typed config for processing root, vault root, category aliases, recorder identity, `odin` endpoint, model names, retention, and OpenClaw API bind address.
- Config for Obsidian CLI binary, vault repository URL, local vault checkout path, and audio retention.
- Example config with no secrets.
- Secret loading through environment or ignored local file.

Acceptance:

- App can validate config and print redacted effective settings.
- Missing vault, processing root, and `odin` endpoint errors are actionable.

## Milestone 1: Safe Ingestion And Transcription

### LGB-003 - SQLite Ledger

Status: Ready after LGB-002

Dependencies: LGB-002

Deliverables:

- Schema for recording jobs, transcripts, log entries, daily logs, dead letters, and action audit records.
- Transaction helpers and migrations.
- Unique constraints for checksum and canonical daily log date.

Acceptance:

- Duplicate source audio cannot create duplicate jobs.
- State transitions are validated and recoverable.

### LGB-004 - Path Builders

Status: Ready after LGB-002

Dependencies: LGB-002

Deliverables:

- Pure functions for processing paths and Obsidian paths.
- Month, weekday, timestamp, and slug formatting.
- Canonical daily log path generator.

Acceptance:

- Tests cover PRD examples exactly.
- Invalid path variants are rejected for daily logs.

### LGB-005 - Sony Mount Probe

Status: Ready after LGB-002

Dependencies: LGB-002

Deliverables:

- Mount detector for Sony ICD-PX370.
- Probe that validates volume identity before enqueueing.
- No processing if an unrelated volume mounts.

Acceptance:

- A mount event can be simulated in tests.
- The probe exits quickly and logs a clear result.

### LGB-006 - Copy And Dedupe Ingest

Status: Blocked

Dependencies: LGB-003, LGB-005

Deliverables:

- Copy new recordings to processing inbox.
- Compute SHA-256.
- Preserve original files on recorder during initial ingest.
- Never delete recorder files before the 24-hour retention cleanup gate.
- Move jobs through `discovered` and `copied`.

Acceptance:

- Reconnecting the recorder does not duplicate known recordings.
- Failed copies remain recoverable.
- Initial ingest does not delete source files from the recorder.

### LGB-007 - Odin Job Contract

Status: Ready after LGB-002

Dependencies: LGB-002

Deliverables:

- Define submit, status, result, and health endpoints for `odin`.
- Error model for offline, queued, failed, and succeeded jobs.
- JSON schema for ASR and diarization output.

Acceptance:

- Mac Mini side can queue while `odin` is offline.
- Worker responses include model, timing, and segment metadata.

### LGB-008 - ASR Worker

Status: Blocked

Dependencies: LGB-007

Deliverables:

- faster-whisper worker on `odin`.
- Configurable model, device, compute type, VAD, and language.
- Complete consumption of lazy segment results before success.

Acceptance:

- A test audio file returns text, segments, timestamps, and ASR metadata.
- Health endpoint reports model readiness and GPU availability.

### LGB-009 - Transcript Persistence

Status: Blocked

Dependencies: LGB-003, LGB-008

Deliverables:

- Store transcript JSON outside Obsidian by job ID.
- Link transcript path and model metadata in the ledger.
- Transition jobs to `transcribed`.

Acceptance:

- Restarting the daemon does not lose completed transcripts.

## Milestone 2: Deterministic Routing

### LGB-010 - Prefix Classifier

Status: Blocked

Dependencies: LGB-009

Deliverables:

- Normalize first 20 words.
- Strip configured filler words.
- Match exact aliases for log, meeting, and categories.
- Add constrained fuzzy aliases for safe log-entry variants.

Acceptance:

- PRD examples route correctly.
- Unknown prefixes become dead letters.
- Fuzzy matching cannot turn arbitrary speech into a category.

### LGB-011 - Markdown Renderers

Status: Ready after LGB-004

Dependencies: LGB-004

Deliverables:

- Frontmatter writer.
- Templates for inbox log entries, category notes, meetings, daily logs, and dead letters.
- Atomic write helper.
- No source-audio links in rendered Obsidian content unless a non-path job ID is needed for audit.

Acceptance:

- Rendered Markdown matches PRD structure.
- Atomic writes do not leave partial files on failure.
- Generated notes do not expose retained audio paths.

### LGB-025 - Obsidian CLI Vault Workflow

Status: Ready after LGB-002

Dependencies: LGB-002

Deliverables:

- Resolve and validate Obsidian CLI binary.
- Configure vault repo as `https://github.com/bprager/obs-vault.git`.
- Manage local vault checkout/sync path.
- Provide pre-write sync/status and post-write commit/push workflow.
- Serialize vault writes to avoid conflicting generated changes.
- Surface CLI failures as recoverable job states.

Acceptance:

- A test vault sync can pull, write a generated file, commit/push or dry-run as configured, and report status.
- No GitHub token or CLI credential is stored outside `.env` or the user's existing credential manager.

### LGB-012 - Log Inbox Writer

Status: Blocked

Dependencies: LGB-003, LGB-010, LGB-011, LGB-025

Deliverables:

- Write `log_entry_inbox` notes under `10 - Logs/00 - Inbox`.
- Strip spoken log prefix.
- Record ledger `log_entry` row.
- Write through the local Obsidian vault workflow.

Acceptance:

- Log entries remain pending until consolidation.

### LGB-013 - Category Note Writer

Status: Blocked

Dependencies: LGB-010, LGB-011, LGB-025

Deliverables:

- Write notes into configured category folders.
- Strip category prefix.
- Record source audio and transcript metadata.
- Write through the local Obsidian vault workflow.

Acceptance:

- `idea`, `task`/`todo`, `research`/`question`, and `reminder` route to expected folders.

### LGB-014 - Dead-Letter Writer

Status: Blocked

Dependencies: LGB-003, LGB-010, LGB-011, LGB-025

Deliverables:

- Write unknown transcripts into `99 - Dead Letters`.
- Set `delete_after` to 28 days.
- Add status and possible rescue actions.

Acceptance:

- Unknown prefixes are retained and reviewable.
- No hard delete occurs as part of MVP.

## Milestone 3: Log Consolidation

### LGB-015 - Consolidation Engine

Status: Blocked

Dependencies: LGB-003, LGB-004, LGB-011, LGB-012, LGB-025

Deliverables:

- Track open log date.
- Consolidate dates earlier than a later-arriving log.
- Render final daily logs in timestamp order.
- Record daily consolidation metadata.
- Sync generated daily logs through the Obsidian CLI vault workflow.

Acceptance:

- The canonical path exactly matches `06 - Timestamps/YYYY/MM-Month/YYYY-MM-DD-Weekday-Log.md`.
- Only one final daily log exists for a date.

### LGB-016 - Late-Arrival Rebuild

Status: Blocked

Dependencies: LGB-015

Deliverables:

- Detect late entries for already consolidated dates.
- Mark entry as `late_arrival`.
- Re-render the canonical daily log atomically.
- Update `entry_count` and checksum.

Acceptance:

- Late entries are inserted in timestamp order.
- No duplicate daily log variant is created.

## Milestone 4: Meetings

### LGB-017 - Meeting Diarization

Status: Blocked

Dependencies: LGB-007, LGB-010

Deliverables:

- Diarize recordings classified as meetings.
- Keep model configurable between PRD baseline and newer pyannote options.
- Persist diarization output and speaker labels.

Acceptance:

- Meeting jobs include transcript segments and diarization turns.
- Missing Hugging Face token or model access fails clearly and recoverably.

### LGB-018 - Meeting Note Renderer

Status: Blocked

Dependencies: LGB-011, LGB-017, LGB-025

Deliverables:

- Write meeting notes under `30 - Meetings/YYYY/MM-Month`.
- Include participant mapping placeholders, summary placeholders, decisions, action items, and transcript.
- Optionally link meetings into daily timestamp notes after consolidation exists.
- Write through the local Obsidian vault workflow.

Acceptance:

- Speaker labels appear as `SPEAKER_00`, `SPEAKER_01`, etc.

## Milestone 5: OpenClaw Observability

### LGB-019 - Status API

Status: Blocked

Dependencies: LGB-003, LGB-012, LGB-014, LGB-015

Deliverables:

- `GET /health`
- `GET /jobs`
- `GET /jobs/{id}`
- `GET /logs/inbox`
- `GET /logs/open-date`
- `GET /logs/consolidated/latest`
- `GET /dead-letters`

Acceptance:

- OpenClaw can observe queue, inbox, latest consolidated log, and dead letters without shell access.

### LGB-020 - Bounded Action API

Status: Blocked

Dependencies: LGB-019

Deliverables:

- `POST /jobs/{id}/reprocess`
- `POST /dead-letters/{id}/rescue`
- `POST /logs/{date}/rebuild`
- Audit log for every requested action.

Acceptance:

- Actions are validated, idempotent, and scoped.
- No endpoint performs arbitrary shell execution or direct hard delete.

### LGB-021 - launchd Packaging

Status: Blocked

Dependencies: LGB-006, LGB-019

Deliverables:

- `launchd` plist template using `StartOnMount`.
- Runtime user guidance respecting `clawdbot` OpenClaw ownership.
- Log paths and graceful shutdown behavior.
- Cleanup schedule for 24-hour audio retention.

Acceptance:

- Mount trigger starts only the lightweight probe.
- OpenClaw services are not started as `bernd`.

### LGB-026 - Audio Retention Cleanup

Status: Blocked

Dependencies: LGB-003, LGB-006, LGB-009, LGB-012, LGB-013, LGB-014, LGB-018, LGB-025

Deliverables:

- Track local audio and recorder-side audio cleanup eligibility.
- Delete or trash local copied audio after 24 hours only after processing, Markdown write, and vault sync are confirmed.
- Delete Sony-recorder source files after 24 hours only after checksum, transcript, derived note, and vault sync are confirmed.
- Record cleanup attempts, successes, failures, and retry eligibility in SQLite.
- Provide cleanup status data for the status API.

Acceptance:

- Audio is not linked from Obsidian notes.
- No audio is deleted before 24 hours.
- No audio is deleted if processing or vault sync is incomplete.
- Reconnecting the recorder after 24 hours cleans eligible source files and leaves ineligible files untouched.
- Cleanup failures are visible and retryable.

## Milestone 6: Verification And Refinement

### LGB-022 - End-To-End Acceptance Tests

Status: Blocked

Dependencies: LGB-012, LGB-013, LGB-014, LGB-016, LGB-018, LGB-020, LGB-025, LGB-026

Deliverables:

- Fixture transcripts for each routing class.
- Simulated multi-day log consolidation.
- Late-arrival rebuild test.
- Dead-letter rescue test.
- Offline `odin` queue test.
- Obsidian CLI vault sync test.
- 24-hour local and recorder-side audio cleanup test.

Acceptance:

- PRD acceptance criteria are covered by automated tests or explicit manual test scripts.

### LGB-023 - Optional Open Log Preview

Status: Later

Dependencies: LGB-015

Deliverables:

- Optional `10 - Logs/00 - Inbox/Open-Log-Preview.md`.
- Clear marker that the note is generated and non-canonical.

Acceptance:

- Current-day visibility improves without violating the one-final-log invariant.

### LGB-024 - Summaries And Action Extraction

Status: Later

Dependencies: LGB-018, LGB-020

Deliverables:

- Optional summaries for meetings and notes.
- Optional action item extraction.
- Human-review workflow before writing derived content into canonical notes.

Acceptance:

- Generated summaries never overwrite raw transcript or staged source content.
