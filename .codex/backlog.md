# Backlog

Updated: 2026-04-29

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

Status: Completed

Dependencies: LGB-002

Deliverables:

- Initial SQLite schema and migration table.
- Recording job discovery table keyed by checksum.
- Idempotent checksum-based discovery recording.
- Schema for recording jobs, transcripts, log entries, daily logs, dead letters, and action audit records.
- Transaction helpers and migrations.
- Unique constraints for checksum and canonical daily log date.

Acceptance:

- `ingest-dry-run --record-discovery` records discovered recorder files without copying or deleting audio.
- Duplicate source audio cannot create duplicate jobs.
- State transitions are validated and recoverable.
- Ledger tracks copied, transcribed, routed, consolidated, canonical daily log path, and late-arrival timestamps for the implemented workflow.

### LGB-004 - Path Builders

Status: Completed

Dependencies: LGB-002

Deliverables:

- Pure functions for processing paths and Obsidian paths.
- Month, weekday, timestamp, and slug formatting.
- Test-vault inbox, category, meeting, and dead-letter paths.
- Canonical daily log path generator.

Acceptance:

- Tests cover PRD examples exactly.
- Invalid path variants are rejected for daily logs.
- Canonical daily log paths render as `06 - Timestamps/YYYY/MM-Month/YYYY-MM-DD-Weekday-Log.md`.

### LGB-005 - Sony Mount Probe

Status: Completed

Dependencies: LGB-002

Deliverables:

- Read-only recorder discovery CLI.
- Mount detector for Sony ICD-PX370.
- Probe that validates volume identity before enqueueing.
- MP3 listing that ignores macOS `._*` sidecar files.
- Sony filename timestamp parser.
- No processing if an unrelated volume mounts.

Acceptance:

- `logbook recorder-discover --env .env` validates the connected recorder without modifying files.
- A mount event can be simulated in tests.
- The probe exits quickly and logs a clear result.

### LGB-006 - Copy And Dedupe Ingest

Status: Completed

Dependencies: LGB-003, LGB-005

Deliverables:

- Checksum-based ingest planning dry run.
- Known-vs-new comparison against the SQLite ledger.
- Idempotent copy into `LOGBOOK_PROCESSING_ROOT/inbox`.
- Post-copy SHA-256 verification before ledger status changes to `copied`.
- Copy new recordings to processing inbox.
- Compute SHA-256.
- Preserve original files on recorder during initial ingest.
- Never delete recorder files before the 24-hour retention cleanup gate.
- Move jobs through `discovered` and `copied`.

Acceptance:

- Dry run reports new and known recordings without copying or deleting audio.
- Reconnecting the recorder does not duplicate known recordings.
- Repeat copy run skips already-copied recordings.
- Failed copies remain recoverable.
- Initial ingest does not delete source files from the recorder.

### LGB-007 - Odin Job Contract

Status: Completed

Dependencies: LGB-002

Deliverables:

- Typed request/response/result contract models.
- Fake `odin` client for local integration testing.
- HTTP `odin` client boundary using scoped bearer-token auth.
- Define submit, status, result, and health endpoints for `odin`.
- Error model for offline, queued, failed, and succeeded jobs.
- JSON schema for ASR and diarization output.

Acceptance:

- Mac Mini side can queue while `odin` is offline.
- Worker responses include model, timing, and segment metadata.
- Fake client can generate transcript JSON without GPU services.

### LGB-008 - ASR Worker

Status: Completed

Dependencies: LGB-007

Deliverables:

- faster-whisper worker on `odin`.
- Configurable model, device, compute type, VAD, and language.
- Complete consumption of lazy segment results before success.

Acceptance:

- A test audio file returns text, segments, timestamps, and ASR metadata.
- Health endpoint reports model readiness and GPU availability.

### LGB-009 - Transcript Persistence

Status: Completed

Dependencies: LGB-003, LGB-008

Deliverables:

- Fake transcript persistence under `LOGBOOK_PROCESSING_ROOT/transcripts`.
- Ledger fields for `odin_job_id`, `transcript_path`, `transcribed_at`, and `asr_model`.
- Store transcript JSON outside Obsidian by job ID.
- Link transcript path and model metadata in the ledger.
- Transition jobs to `transcribed`.

Acceptance:

- Restarting the daemon does not lose completed transcripts.
- Fake transcript pass writes transcript JSON and updates copied jobs to `transcribed`.

## Milestone 2: Deterministic Routing

### LGB-010 - Prefix Classifier

Status: Completed

Dependencies: LGB-009

Deliverables:

- Normalize first 20 words.
- Strip built-in filler words.
- Match exact aliases for log, meeting, and categories.
- Add constrained fuzzy aliases for safe log-entry variants.

Acceptance:

- PRD examples route correctly.
- Unknown prefixes become dead letters.
- Fuzzy matching cannot turn arbitrary speech into a category.

### LGB-011 - Markdown Renderers

Status: Completed

Dependencies: LGB-004

Deliverables:

- Frontmatter writer.
- Templates for routed transcript notes in the test-vault slice.
- Atomic write helper.
- No source-audio links in rendered Obsidian content unless a non-path job ID is needed for audit.

Acceptance:

- Rendered Markdown matches PRD structure.
- Atomic writes do not leave partial files on failure.
- Generated notes do not expose retained audio paths.

### LGB-025 - Obsidian CLI Vault Workflow

Status: Completed

Dependencies: LGB-002

Deliverables:

- Resolve and validate Obsidian CLI binary.
- Configure vault repo as `https://github.com/bprager/obs-vault.git`.
- Manage local vault checkout/sync path.
- Add `vault-preflight` for non-writing validation.
- Verify the configured `obsidian-cli` vault name is registered before writer use.
- Add configurable Obsidian CLI command templates for sync, status, commit, and push.
- Add serialized write lock around vault workflow commands.
- Add optional `obsidian-cli create` note writer for registered Obsidian vaults.
- Add `--job-id` routing to constrain first production vault writes.
- Provide pre-write sync/status and post-write commit/push workflow.
- Serialize vault writes to avoid conflicting generated changes.
- Surface CLI failures as recoverable job states.

Acceptance:

- A test vault sync can pull, write a generated file, commit/push or dry-run as configured, and report status.
- No GitHub token or CLI credential is stored outside `.env` or the user's existing credential manager.
- Missing CLI or missing vault path fails preflight before writing.
- `obsidian-cli` writer uses vault names and is gated on Obsidian vault registration.
- Local `obs-vault` checkout exists at `/Users/bernd/Obsidian/obs-vault`.
- First real-vault note write succeeded for ledger job 17 through `obsidian-cli`.
- Batch backfill to the real vault succeeded for all 17 inbox notes.
- Git workflow templates are configured for pull, stage, status, commit, and push.

### LGB-012 - Log Inbox Writer

Status: Completed

Dependencies: LGB-003, LGB-010, LGB-011, LGB-025

Deliverables:

- Write `log_entry_inbox` notes under `10 - Logs/00 - Inbox`.
- Strip spoken log prefix.
- Record routed ledger status and relative Obsidian-style path.
- Write through the local Obsidian vault workflow.

Acceptance:

- Log entries remain pending until consolidation.
- Test-vault pilot wrote 17 log inbox notes from fake transcripts with no audio paths exposed.
- Real-vault pilot wrote and pushed 17 log inbox notes through `obsidian-cli`.

### LGB-013 - Category Note Writer

Status: Completed

Dependencies: LGB-010, LGB-011, LGB-025

Deliverables:

- Write notes into configured category folders.
- Strip category prefix.
- Record source audio and transcript metadata.
- Write through the local Obsidian vault workflow.

Acceptance:

- `idea`, `task`/`todo`, `research`/`question`, and `reminder` route to expected folders.
- Category notes are written under `20 - Notes/00 - Inbox/{category}` with the spoken prefix stripped.
- Ledger records `category_written`, the `category:{name}` classification, and the relative Obsidian path.

### LGB-014 - Dead-Letter Writer

Status: Completed

Dependencies: LGB-003, LGB-010, LGB-011, LGB-025

Deliverables:

- Write unknown transcripts into `99 - Dead Letters`.
- Set `delete_after` to 28 days.
- Add status and possible rescue actions.

Acceptance:

- Unknown prefixes are retained and reviewable.
- No hard delete occurs as part of MVP.
- Dead-letter notes are written under `99 - Dead Letters` with `review_status` and a 28-day `delete_after`.
- Ledger records `dead_letter_written`, the `dead_letter` classification, and the relative Obsidian path.

## Milestone 3: Log Consolidation

### LGB-015 - Consolidation Engine

Status: Completed

Dependencies: LGB-003, LGB-004, LGB-011, LGB-012, LGB-025

Deliverables:

- Track open log date.
- Manual consolidation CLI for routed log entries.
- Consolidate routed log entries by date.
- Render final daily logs in timestamp order.
- Record each job's canonical daily log path and consolidated timestamp in the ledger.
- Sync generated daily logs through the Obsidian vault Git workflow.

Acceptance:

- The canonical path exactly matches `06 - Timestamps/YYYY/MM-Month/YYYY-MM-DD-Weekday-Log.md`.
- Only one final daily log exists for a date.
- Real vault pilot generated and pushed 3 daily logs from 17 routed inbox notes.

### LGB-016 - Late-Arrival Rebuild

Status: Completed

Dependencies: LGB-015

Deliverables:

- Detect late entries for already consolidated dates.
- Mark entry as `late_arrival`.
- Re-render the canonical daily log atomically.
- Update `entry_count` and checksum.

Acceptance:

- Late entries are inserted in timestamp order.
- No duplicate daily log variant is created.
- Consolidation detects a new inbox entry for an already consolidated date, marks it as a late arrival, and atomically rebuilds the existing canonical daily log.

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

Status: Completed

Dependencies: LGB-003, LGB-012, LGB-014, LGB-015

Deliverables:

- `GET /health`
- `GET /jobs`
- `GET /jobs/{id}`
- `GET /logs/inbox`
- `GET /logs/open-date`
- `GET /logs/consolidated/latest`
- `GET /dead-letters`
- FastAPI app factory with Swagger UI at `/docs`, ReDoc at `/redoc`, and OpenAPI at `/openapi.json`.
- Optional bearer-token read auth through `LOGBOOK_READ_TOKEN`.

Acceptance:

- OpenClaw can observe queue, inbox, latest consolidated log, and dead letters without shell access.
- Status responses avoid local source audio paths, copied audio paths, and transcript paths.
- Swagger UI is configured with Logbook API metadata and bearer auth.

### LGB-020 - Bounded Action API

Status: Completed

Dependencies: LGB-019

Deliverables:

- `POST /jobs/{id}/reprocess`
- `POST /dead-letters/{id}/rescue`
- `POST /logs/{date}/rebuild`
- Audit log for every requested action.
- SQLite `action_audit` table for accepted bounded action intents.
- Separate action-token auth through `LOGBOOK_ACTION_TOKEN`.
- Optional per-target action idempotency key for safe OpenClaw retries.

Acceptance:

- Actions are validated, idempotent, and scoped.
- No endpoint performs arbitrary shell execution or direct hard delete.
- Action endpoints record auditable intent without mutating job state inline.
- Invalid targets are rejected before audit creation.
- Repeated requests with the same idempotency key return the existing audit record.

### LGB-021 - launchd Packaging

Status: Ready

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
- Compute cleanup eligibility from SQLite ledger timestamps, not recorder mtimes or YYMMDD filenames.
- Delete or trash local copied audio after 24 hours only after processing, Markdown write, and vault sync are confirmed.
- Delete Sony-recorder source files after 24 hours only after checksum, transcript, derived note, and vault sync are confirmed.
- Record cleanup attempts, successes, failures, and retry eligibility in SQLite.
- Provide cleanup status data for the status API.

Acceptance:

- Audio is not linked from Obsidian notes.
- No audio is deleted before 24 hours.
- Clock-skewed or manually corrected recorder files are not deleted until their ledger `cleanup_eligible_at` is reached.
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
