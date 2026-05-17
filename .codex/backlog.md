# Backlog

Updated: 2026-05-17

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
LGB-003 + LGB-009 + LGB-017 + LGB-018 + LGB-019 + LGB-024 -> LGB-027 Proof-carrying memory graph
LGB-027 -> LGB-028 Action candidate resolution
LGB-027 -> LGB-029 Memory graph health and drift check
LGB-015 + LGB-021 + LGB-025 -> LGB-035 Daily log entity linker
LGB-014 + LGB-015 + LGB-020 + LGB-035 -> LGB-036 Dead letter management script
LGB-021 + LGB-022 -> LGB-030 Production launchd rollout
LGB-019 + LGB-021 -> LGB-031 Prometheus metrics and scrape integration
LGB-003 + LGB-026 -> LGB-032 Saga backups and restore drill
LGB-027 + LGB-029 -> LGB-033 Memory graph prune and drift repair
LGB-022 + LGB-030 + LGB-031 + LGB-032 + LGB-033 -> LGB-034 0.2.0 release readiness
LGB-034 + LGB-035 + LGB-036 -> LGB-037 1.0.0 stable release promotion
LGB-037 -> LGB-038 1.0.1 mount ingest hardening patch release
LGB-003 + LGB-019 + LGB-021 + LGB-031 + LGB-038 -> LGB-039 Pipeline observer and watch program
LGB-039 -> LGB-040 1.1.0 quality gate release
LGB-039 + LGB-040 -> LGB-041 Modern observer UI surfaces
LGB-041 -> LGB-042 1.2.0 modern observer release
```

## Milestone 0: Repo And Product Foundations

### LGB-001 - Foundations

Status: Completed

Dependencies: none

Deliverables:

- Confirm implementation language and package layout.
- Define local development commands.
- Add baseline test and lint tooling.
- Keep generated data and secrets out of git.

Acceptance:

- A new Codex session can find context, decisions, backlog, and PRD quickly.
- Repo has clear instructions for safe local-first development.

Completion note:

- Completed as part of the `0.1.0` planning release and subsequent MVP implementation. The repo now has project context, decisions, backlog, PRD, README, tests, lint tooling, ignored local secrets, and repeatable development commands.

### LGB-002 - Configuration

Status: Completed

Dependencies: LGB-001

Deliverables:

- Typed config for processing root, vault root, category aliases, recorder identity, `odin` endpoint, model names, retention, and OpenClaw API bind address.
- Config for Obsidian CLI binary, vault repository URL, local vault checkout path, and audio retention.
- Example config with no secrets.
- Secret loading through environment or ignored local file.

Acceptance:

- App can validate config and print redacted effective settings.
- Missing vault, processing root, and `odin` endpoint errors are actionable.

Completion note:

- Completed through the typed `.env`/`AppConfig` implementation, tracked `.env.example`, recorder/vault/`odin`/retention/OpenClaw token settings, Memgraph URI support, and live validation against the configured host-local `.env`.

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
- Deployable FastAPI worker app with `/health`, `POST /jobs`, and `GET /jobs/{odin_job_id}/result`.
- `serve-odin-worker` command for starting the worker on the target host.
- Client-side `odin-health` probe for the configured worker health endpoint.
- Client-side `transcribe-copied` command that submits copied recordings through the HTTP `odin` client.

Acceptance:

- A test audio file returns text, segments, timestamps, and ASR metadata.
- Health endpoint reports model readiness and GPU availability.
- Live `odin-health --env .env` succeeds against the actual host.
- Live `transcribe-copied --env .env` succeeds for at least one copied test recording without using the fake client.

Current note:

- Live `odin-health --env .env` passes against `http://192.168.1.3:8765`; the worker now runs on `odin` as enabled user systemd service `logbook-odin-worker.service`.
- Live `transcribe-copied` validation passed on 2026-04-29 against an isolated copied test recording in `/Users/bernd/VoiceIngest/odin-live-validation-20260429-190415`; transcript JSON included text, segment timestamps, language, and `large-v3` ASR metadata.

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

Status: Completed

Dependencies: LGB-007, LGB-010

Deliverables:

- Diarize recordings classified as meetings.
- Keep model configurable between PRD baseline and newer pyannote options.
- Persist diarization output and speaker labels.
- `logbook fake-diarize-meetings` command for local contract validation without GPU services.
- SQLite fields for diarization path, timestamp, and model metadata.

Acceptance:

- Meeting jobs include transcript segments and diarization turns.
- Missing Hugging Face token or model access fails clearly and recoverably.
- Non-meeting transcripts are skipped without mutating job state.
- Diarization results without speaker labels fail without mutating job state.

### LGB-018 - Meeting Note Renderer

Status: Completed

Dependencies: LGB-011, LGB-017, LGB-025

Deliverables:

- Write meeting notes under `30 - Meetings/YYYY/MM-Month`.
- Include participant mapping placeholders, summary placeholders, decisions, action items, and transcript.
- Optionally link meetings into daily timestamp notes after consolidation exists.
- Write through the local Obsidian vault workflow.
- Require diarization before rendering meeting notes so speaker labels are available.
- Strip the spoken `meeting` prefix from the first transcript segment in rendered notes.

Acceptance:

- Speaker labels appear as `SPEAKER_00`, `SPEAKER_01`, etc.
- Meeting notes do not expose source audio paths or filenames.
- Transcribed meeting jobs without diarization fail safely without mutating job state.

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

Status: Completed

Dependencies: LGB-006, LGB-019

Deliverables:

- `launchd` plist template using `StartOnMount`.
- Runtime user guidance respecting `clawdbot` OpenClaw ownership.
- Log paths and graceful shutdown behavior.
- Cleanup schedule for 24-hour audio retention.
- `logbook launchd-render` command that writes host-local plists under `LOGBOOK_PROCESSING_ROOT/launchd` by default.
- `logbook retention-status` command for the scheduled no-delete retention audit placeholder.

Acceptance:

- Mount trigger starts only the lightweight probe.
- OpenClaw services are not started as `bernd`.
- API service plist uses launchd keepalive and a bounded shutdown timeout.
- Retention job runs on schedule but does not delete audio before LGB-026.

### LGB-026 - Audio Retention Cleanup

Status: Completed

Dependencies: LGB-003, LGB-006, LGB-009, LGB-012, LGB-013, LGB-014, LGB-018, LGB-025

Deliverables:

- Track local audio and recorder-side audio cleanup eligibility.
- Compute cleanup eligibility from SQLite ledger timestamps, not recorder mtimes or YYMMDD filenames.
- Delete or trash local copied audio after 24 hours only after processing, Markdown write, and vault sync are confirmed.
- Delete Sony-recorder source files after 24 hours only after checksum, transcript, derived note, and vault sync are confirmed.
- Record cleanup attempts, successes, failures, and retry eligibility in SQLite.
- Provide cleanup status data for the status API.
- `cleanup-plan` command for read-only eligibility inspection.
- `cleanup-audio --execute` for explicit local cleanup.
- `cleanup-audio --execute --include-recorder` for explicit recorder-side cleanup.
- `GET /cleanup/audio` read-only FastAPI endpoint.

Acceptance:

- Audio is not linked from Obsidian notes.
- No audio is deleted before 24 hours.
- Clock-skewed or manually corrected recorder files are not deleted until their ledger `cleanup_eligible_at` is reached.
- No audio is deleted if processing or vault sync is incomplete.
- Reconnecting the recorder after 24 hours cleans eligible source files and leaves ineligible files untouched.
- Cleanup failures are visible and retryable.
- Cleanup planning is read-only by default and does not persist eligibility or delete audio.
- Recorder-side deletion verifies the source path is under the configured recorder folder and checksum matches.

## Milestone 6: Verification And Refinement

### LGB-022 - End-To-End Acceptance Tests

Status: Completed

Dependencies: LGB-012, LGB-013, LGB-014, LGB-016, LGB-018, LGB-020, LGB-025, LGB-026

Deliverables:

- Fixture transcripts for each routing class.
- Simulated multi-day log consolidation.
- Late-arrival rebuild test.
- Dead-letter rescue test.
- Offline `odin` queue test.
- Obsidian CLI vault sync test.
- 24-hour local and recorder-side audio cleanup test.
- Guarded `mark-vault-synced` dry-run/execute command that proves generated vault paths are in pushed vault `HEAD` before setting `vault_synced_at`.

Acceptance:

- PRD acceptance criteria are covered by automated tests or explicit manual test scripts.
- Existing pushed vault notes can be proven and marked as synced without deleting audio.

Completion note:

- First production batch acceptance completed on 2026-05-01. Guarded local cleanup and recorder-side cleanup both reached 0 pending actions for the 17 consolidated, vault-synced jobs. Follow-up recorder discovery showed 7 newer MP3 files remaining for the next ingest batch, and verification passed with 78 tests OK, `ruff check .` OK, and memory graph health `status=ok`.

### LGB-023 - Optional Open Log Preview

Status: Completed

Dependencies: LGB-015

Deliverables:

- Optional `10 - Logs/00 - Inbox/Open-Log-Preview.md`.
- Clear marker that the note is generated and non-canonical.
- `logbook open-log-preview` command with filesystem and `obsidian-cli` writer support.
- Date override for testing or explicitly previewing a selected day.

Acceptance:

- Current-day visibility improves without violating the one-final-log invariant.
- Preview note renders entries from routed or already consolidated log jobs without creating another canonical daily log.

### LGB-024 - Summaries And Action Extraction

Status: Completed

Dependencies: LGB-018, LGB-020

Deliverables:

- Optional summaries for meetings and notes.
- Optional action item extraction.
- Human-review workflow before writing derived content into canonical notes.
- `logbook extract-insights` command that writes JSON artifacts under `LOGBOOK_PROCESSING_ROOT/insights`.
- Non-canonical review notes under `40 - Reviews/Logbook Insights`.
- Deterministic first-pass summary and action candidate extraction for task/category notes, logs, and diarized meetings.

Acceptance:

- Generated summaries never overwrite raw transcript or staged source content.
- Review artifacts are marked `needs_review` and `canonical=false`.
- Source notes and canonical daily logs are not modified by extraction.

## Milestone 7: Proof-Carrying Memory

### LGB-027 - Proof-Carrying Memory Graph

Status: Completed

Priority: P0

Dependencies: LGB-003, LGB-009, LGB-017, LGB-018, LGB-019, LGB-024

Deliverables:

- `logbook memory-graph-sync --env .env` dry-run planner that reads SQLite, transcripts, diarization artifacts, generated notes, and insight artifacts without writing.
- Explicit `--execute` mode that idempotently upserts proof-carrying memory nodes and relationships into Memgraph.
- Stable graph IDs for `LogbookJob`, `TranscriptSegment`, `GeneratedNote`, `ActionCandidate`, `Decision`, `Topic`, `Person`, `Project`, and `SourceEvidence`.
- Evidence model that links every derived action, decision, topic, person, and project back to job ID, transcript segment offsets, generated note path, artifact checksum, and source timestamp.
- No source audio paths or recorder paths in graph nodes, relationships, API responses, or generated notes.
- Initial read-only query set for open loops, unresolved action candidates, recent decisions, topic trails, and weekly memory diffs.
- Test fixtures proving idempotent sync, dry-run safety, evidence completeness, and path privacy.

Acceptance:

- Dry-run reports planned node and relationship changes without mutating Memgraph.
- Re-running `--execute` against the same ledger/artifacts is idempotent.
- Every generated memory object has at least one `SUPPORTED_BY`/`DERIVED_FROM` evidence path back to a Logbook job and source artifact.
- The graph can answer "what did I promise?", "what remains unresolved?", and "what changed this week?" using only local data.
- SQLite remains the processing source of truth; Memgraph is the query/memory layer.
- OpenClaw can consume graph-backed memory through bounded read APIs without shell access.

Implementation notes:

- Added `logbook memory-graph-sync` dry-run planner and explicit `--execute` Memgraph upsert path with stable `MERGE` IDs.
- Added `logbook memory-graph-query` and FastAPI `/memory/*` read endpoints for open loops, unresolved actions, recent decisions, topic trails, and weekly diffs.
- Added path-privacy and evidence-completeness tests proving source audio paths do not leave the ledger/transcript artifacts.

### LGB-028 - Action Candidate Resolution

Status: Completed

Priority: P0

Dependencies: LGB-027

Deliverables:

- Durable SQLite review table for memory action candidate resolution state.
- Dry-run-first `logbook memory-action-resolve` command with explicit `--execute` write mode.
- Token-protected FastAPI `POST /memory/actions/{action_id}/resolve` endpoint.
- Memory graph overlay that marks resolved `ActionCandidate` nodes and excludes them from open-loop queries.
- Audit records for API and CLI resolution writes.

Acceptance:

- Resolving an action candidate does not mutate transcripts, generated notes, canonical logs, source audio, or recorder audio.
- Resolved action candidates disappear from `/memory/open-loops` and `memory-graph-query --query open-loops`.
- Resolution writes are durable in SQLite and visible on the next graph build.
- Resolution endpoints remain bounded by action-token auth in the API and dry-run-first behavior in the CLI.

### LGB-029 - Memory Graph Health And Drift Check

Status: Completed

Priority: P0

Dependencies: LGB-027

Deliverables:

- Read-only local-vs-live Memgraph comparison for Logbook-owned graph nodes and relationships.
- `logbook memory-graph-health --env .env` CLI command that reports plan counts, live counts, drift by label/type, and Memgraph reachability.
- FastAPI `GET /memory/graph-health` endpoint registered in Swagger/OpenAPI.
- Tests for matching counts, drift detection, missing Memgraph config, and OpenAPI registration.

Acceptance:

- The health check never writes to Memgraph, SQLite, generated notes, source audio, or recorder audio.
- A freshly synced graph reports `status=ok` when live counts match the local plan.
- Missing or unreachable Memgraph is reported explicitly without blocking local plan generation.
- Count drift is visible by node label and relationship type so the next operator action is clear.

## Milestone 8: Production Hardening

### LGB-030 - Production launchd Rollout

Status: Completed on 2026-05-03

Priority: P0

Dependencies: LGB-021, LGB-022

Deliverables:

- Install or document the exact launchd bootstrap commands for the Logbook API, mount probe, and retention audit on `mimir`.
- Verify rendered plist paths, labels, logs, environment file references, and restart behavior.
- Keep OpenClaw runtime ownership separate: do not start OpenClaw gateway or node services as `bernd`.
- Add an operational check command sequence for launchd status, API health, mount probe dry run, and retention audit output.

Acceptance:

- A fresh operator can install, inspect, stop, and rollback the Logbook launchd jobs without reading implementation code.
- The launchd rollout does not start OpenClaw services under `bernd`.
- Status/API and recorder probe behavior are verified after bootstrap.

Notes:

- Installed `local.logbook.api`, `local.logbook.recorder.mount-probe`, `local.logbook.retention-audit`, and `local.logbook.entity-linker` as `bernd` user LaunchAgents on `mimir`.
- Moved Logbook API from `127.0.0.1:8787` to `127.0.0.1:8788` because `8787` is already owned by the `clawdbot` CashClaw/OpenClaw adapter.
- Verified `curl http://127.0.0.1:8788/health`, `/metrics`, bounded mount-triggered ingest wiring, and read-only retention audit wiring. The original rollout used discovery-only mount wiring; `1.0.1` changes the mount job to `process-mounted-recorder`.
- Verified OpenClaw gateway/node processes remained owned by `clawdbot`; only Logbook launchd jobs run as `bernd`.

### LGB-031 - Prometheus Metrics And Scrape Integration

Status: Completed on 2026-05-03

Priority: P0

Dependencies: LGB-019, LGB-021

Deliverables:

- Add `/metrics` endpoints for the Logbook recorder/status API and the `odin` worker.
- Expose queue depth, job status counts, failed jobs, dead letters, latest consolidation age, cleanup pending counts, `odin` health, and graph health status.
- Document Prometheus scrape targets on `odin` without reverse-proxying the transcription path through `fenrir`.
- Add alert candidates for stale consolidation, failed transcription, cleanup failures, API down, `odin` down, and graph drift.

Acceptance:

- Metrics are path-safe and do not expose source audio, transcript paths, bearer tokens, or local vault paths.
- Prometheus can scrape the configured endpoints over the trusted LAN or loopback path.
- Alert candidates map to clear operator actions.

Notes:

- Implemented `/metrics` on the Logbook API and `odin` worker using Prometheus text exposition without adding an external dependency.
- Documented internal scrape targets and alert candidates in `docs/metrics.md`.
- Metrics are count/status/model gauges only and avoid source audio, transcript, token, and vault path exposure.

### LGB-032 - Saga Backups And Restore Drill

Status: Completed on 2026-05-03

Priority: P0

Dependencies: LGB-003, LGB-026

Deliverables:

- Define SQLite backup semantics that work with WAL mode and avoid raw copying of a live database.
- Back up the SQLite ledger, configuration templates, generated operational artifacts, and selected non-audio state to `saga`.
- Decide whether quarantined local audio is backed up, excluded, or expired before backup.
- Add a restore drill that validates a copied ledger can be opened and queried without mutating production state.

Acceptance:

- A backup run produces a timestamped, restorable artifact set on `saga`.
- The restore drill proves ledger integrity and expected job counts from a backup copy.
- Secrets and raw source audio are handled according to the documented policy.

Notes:

- Added dry-run-first `backup-run` and read-only `backup-restore-drill` CLI commands.
- SQLite backup uses `sqlite3.Connection.backup`; no raw live WAL database copy is used.
- Live target is `192.168.1.3:/mnt/saga/Napoleon/logbook-backups`, which writes through `odin` to the `saga` NFS mount using `/Users/bernd/.ssh/id_rsa_odin`.
- First production artifact set: `logbook-backup-20260503T155634Z`, 34 ledger jobs, 4 audit records, 42 copied artifacts.
- Restore drill from the remote `saga` copy reported `status=ok`, `integrity_check=ok`, schema version `1`, and job count `34`.
- Policy: live `.env`, secrets, source audio, inbox audio, and quarantined/trash audio are excluded.

### LGB-033 - Memory Graph Prune And Drift Repair

Status: Completed on 2026-05-03

Priority: P0

Dependencies: LGB-027, LGB-029

Deliverables:

- Add a dry-run-first graph prune command that identifies Logbook-owned nodes and relationships present in Memgraph but absent from the current local plan.
- Scope pruning to the Logbook memory namespace only, preserving backlog/project/host graph data.
- Add explicit `--execute` mode with count reporting and before/after health checks.
- Add tests for stale generated notes, rerouted dead letters, and removed evidence relationships.

Acceptance:

- Drift caused by reroutes or removed generated notes can be repaired without resetting unrelated Memgraph data.
- Dry run shows exactly what would be pruned.
- Health reports `status=ok` after prune plus sync.

Notes:

- Implemented as `logbook memory-graph-repair --env .env`, dry-run by default.
- The command compares exact Logbook-owned live node/relationship IDs against the local proof plan, upserts missing planned IDs with `--execute`, and prunes stale Logbook-owned IDs only when `--prune-stale --execute` is set.
- Pruning is scoped to IDs in the Logbook memory namespace and does not reset unrelated Memgraph data.

### LGB-034 - 0.2.0 Release Readiness

Status: Completed on 2026-05-03; tagged and pushed

Priority: P1

Dependencies: LGB-022, LGB-030, LGB-031, LGB-032, LGB-033

Deliverables:

- Prepare `0.2.0` release notes covering live `odin` transcription, real vault operation, retention cleanup, memory graph, placeholder cleanup, and production hardening.
- Update README status from implementation-started language to operational MVP language.
- Confirm final verification commands, vault state, Memgraph health, and retention posture.
- Tag and push the release only after explicit operator approval.

Acceptance:

- The release has a clean working tree except approved local-only files.
- README and changelog accurately describe the live operational surface.
- The release tag is created only after explicit approval.

Notes:

- Prepared `0.2.0` release notes in `docs/releases/0.2.0.md`.
- Updated README from implementation-started language to operational MVP release language.
- Updated `VERSION`, `pyproject.toml`, and `Changelog.md` for `0.2.0`; `v0.2.0` is tagged and pushed after explicit operator approval.
- Added tracked SOPS/age encrypted `secrets.yaml` by explicit operator request.
- Release evidence: `odin-health` healthy, Memgraph health `status=ok`, cleanup has 0 pending local/recorder actions with jobs 25-34 only blocked by retention window, saga restore drill `status=ok`, and vault generated content is clean except `.obsidian/workspace.json`.

### LGB-035 - Daily Log Entity Linker

Status: Completed

Priority: P1

Dependencies: LGB-015, LGB-021, LGB-025

Deliverables:

- Add a dry-run-first CLI that scans canonical daily logs from the last configurable number of calendar months.
- Discover linkable Obsidian entities from `04 - People`, `03 - Objects`, and `06 - Timestamps/Meetings`.
- Link unlinked mentions to existing people, event, and object notes while preserving frontmatter, code blocks, existing wiki links, and Markdown links.
- Add a scheduled launchd job that runs the entity linker daily against the last 3 months.
- Backfill the current last-3-month daily logs in the real vault.

Acceptance:

- The command reports files scanned, links inserted, and matched targets before writing.
- `--execute` is required for vault mutation.
- Scheduled launchd rendering includes the bounded entity-linking command and does not start OpenClaw services.
- The real-vault backfill is committed without staging Obsidian workspace state.

### LGB-036 - Dead Letter Management Script

Status: Completed

Priority: P1

Dependencies: LGB-014, LGB-015, LGB-020, LGB-035

Deliverables:

- Add a repo-local `scripts/manage-dead-letters` wrapper.
- Add a dry-run-first CLI for listing pending dead letters, assigning a dead letter to the log route, and discarding a dead letter.
- When assigning to log, write the rescued inbox note, record an audit action, clear stale vault-sync state, rebuild the canonical daily log for that date, and rerun entity linking.
- When discarding, record an audit action and remove the job from the pending dead-letter list without deleting source audio or recorder audio.

Acceptance:

- List mode is read-only and shows job ID, timestamp, current generated note path, and transcript preview.
- Assign/discard require `--execute` for mutation.
- Rescued log jobs flow through daily consolidation and entity linking in one command.
- Discarded jobs remain auditable in SQLite.

## Milestone 9: Stable Operational Release

### LGB-037 - 1.0.0 Stable Release Promotion

Status: Completed on 2026-05-05; tagged and pushed

Priority: P0

Dependencies: LGB-034, LGB-035, LGB-036

Deliverables:

- Promote project metadata from `0.2.0` to `1.0.0`.
- Update README, changelog, release notes, durable Codex status, and backlog for stable operational release status.
- Confirm current verification commands, vault state, Memgraph health, `odin` health, recorder cleanup posture, and backup/restore evidence.
- Tag and push `v1.0.0` after explicit operator approval.

Acceptance:

- Core CLI/API/ledger/Obsidian path contracts are treated as stable release surfaces.
- The release has fresh verification evidence from tests, lint, live `odin` health, Memgraph health, cleanup planning, recorder discovery, and restore drill.
- Local `.env`, Obsidian workspace state, and unrelated local secret edits are not included in the release commit.

Notes:

- The user approved promotion to `1.0.0` on 2026-05-05 after review confirmed no major capability gaps remained.
- Fresh release evidence includes 98 tests passing, `ruff check .` passing, `odin-health` healthy, Memgraph health `status=ok` with 805 planned/live nodes and 1631 planned/live relationships, cleanup at 0 pending eligible actions, recorder discovery showing 5 retained MP3s for still-gated jobs 45-49, and `saga` restore drill `status=ok` for `logbook-backup-20260505T015132Z` with 39 expected/actual jobs.

### LGB-038 - 1.0.1 Mount Ingest Hardening Patch Release

Status: Completed on 2026-05-05; tagged and pushed

Priority: P0

Dependencies: LGB-037

Deliverables:

- Recover the May 5 meeting recording `260505_0919_01.mp3` through transcription, diarization, Obsidian meeting-note routing, vault sync, ledger state, and Memgraph evidence.
- Change the StartOnMount LaunchAgent from discovery-only probing to bounded `process-mounted-recorder` ingestion.
- Harden live ingest around recorder permission errors, long meeting transcription timeouts, MP3-to-WAV diarization normalization, generated-vault roots, and Obsidian workspace state.
- Add `lessons-learned.md` and update README, launchd docs, release notes, changelog, durable status, decisions, and backlog.
- Tag and push `v1.0.1`.

Acceptance:

- Re-running `process-mounted-recorder --env .env` after recovery is idempotent and finds no unrouted candidates.
- The mounted-recorder LaunchAgent arguments show `process-mounted-recorder --env .env`.
- Job 52 is `meeting_written`, has a generated meeting note in `30 - Meetings`, is vault-synced, and is represented in Memgraph.
- The release has fresh lint, unit test, compile, diff, live vault, launchd, and Memgraph evidence before tagging.

Notes:

- Job 52 wrote `30 - Meetings/2026/05-May/2026-05-05T09-19-00-job-000052-meeting.md` and was pushed to the Obsidian vault in commit `796346c`.
- Jobs 51 and 52 are vault-synced after the recovery run; job 50 remains an inbox entry awaiting normal consolidation.
- `lessons-learned.md` is now the durable place for production failure modes and prevention guidance.

## Milestone 10: Operator Watchability

### LGB-039 - Pipeline Observer And Watch Program

Status: In Progress

Priority: P0

Dependencies: LGB-003, LGB-019, LGB-021, LGB-031, LGB-038

Deliverables:

- Add read-only observer documentation in `docs/pipeline-observer.md`.
- Add observer telemetry beside the SQLite job ledger: pipeline runs, stage
  events, heartbeats, progress metadata, and rolling stage-duration history.
- Add a read-only `GET /observer/snapshot` API that reports current run,
  active stage, recent completions, recent failures, stale heartbeat status,
  health summaries, and 24-hour statistics in one consistent response.
- Add `logbook watch` with live refresh, `--once`, `--json`, filters, no-color
  mode, and API-backed remote mode.
- Use measured progress for byte-copy and countable stages.
- Use historical ETA estimates for transcription, diarization, and other
  stages that cannot report real progress.
- Label progress as `measured`, `estimated`, or `unknown`, including ETA
  confidence and sample count for estimates.
- Add path-safe Prometheus metrics for active run, stale run, active stage,
  progress percent, ETA, recent failures, and stage-duration quantiles.

Acceptance:

- `logbook watch --once --env .env` reports recent finished jobs, current run
  state, failures, successes, durations, and basic statistics without writing to
  SQLite, Obsidian, Memgraph, recorder audio, or local source audio.
- `logbook watch --env .env` refreshes compactly in a normal 80-column terminal
  and falls back to plain text when stdout is not a TTY.
- A currently running mounted-recorder pipeline shows current stage, elapsed
  time, heartbeat age, progress bar, ETA, and whether the percentage is measured
  or estimated.
- Sparse history produces `unknown` or `collecting baseline` rather than a fake
  precise ETA.
- Stale pipeline heartbeat detection is visible in the API, CLI, and metrics.
- Default observer output avoids source audio paths, transcript paths, bearer
  tokens, and transcript text.
- Tests cover snapshot consistency, stale heartbeat detection, ETA fallback,
  privacy redaction, terminal/no-TTY output, JSON output, and Prometheus metrics.

Notes:

- Phase 1 landed on 2026-05-15 with a ledger-derived `GET /observer/snapshot`
  endpoint and `logbook watch --once` / `--json` output. This first slice is
  read-only and reports recent finished jobs, durable failures, dead letters,
  and p50/p90 duration statistics.
- Phase 2 landed on 2026-05-15 with `pipeline_runs`, `pipeline_stage_events`,
  the SQLite pipeline reporter, background run heartbeats, mounted-recorder
  stage instrumentation, active-run/stage observer fields, stale-run detection,
  and path-redacted active-stage details.
- Phase 3 landed on 2026-05-15 with `pipeline_stage_durations`,
  stage-duration materialization from successful stage events, legacy
  start/succeeded event rebuild support, and observer ETA fallback using p50
  history, p90 risk duration, sample count, and confidence. Sparse history is
  still reported as `collecting baseline`.
- Phase 4 landed on 2026-05-15 with measured `progress` events, chunked copy
  byte progress, mounted-recorder count progress for route/consolidate/vault
  sync, and observer rendering that prefers measured progress while preserving
  elapsed time from the stage start.
- Phase 5 landed on 2026-05-15 with live in-place refresh, one-shot and JSON
  modes, local SQLite or remote `--api` snapshots, bearer-token environment
  lookup, status filters, failure/stale exit policies, automatic day/night
  appearance, explicit theme overrides, no-color terminal fallback, and optional
  full terminal dashboard mode via `--ui full` with live key controls.
- The watch CLI and observer API now run bounded read-only Odin and Memgraph
  reachability probes, replacing configured-service `unknown` labels with
  `ok` or `unavailable`.
- The observer is a read-only consumer. Bounded recovery actions stay in the
  existing action API and dead-letter management commands.
- SQLite remains the processing source of truth. Observer telemetry explains
  active work and supports ETAs, but it must not decide idempotency or recovery.

### LGB-040 - 1.1.0 Quality Gate Release

Status: Done

Priority: P0

Dependencies: LGB-039

Deliverables:

- Promote project package metadata, API metadata, and release documentation to
  `1.1.0`.
- Add a versioned `.githooks/pre-commit` hook that runs the repository quality
  gate before commits.
- Add `scripts/quality-gate` for manual and hook-driven verification.
- Add a Markdown lint configuration and dev dependencies for coverage and
  changed-line coverage checks.

Acceptance:

- Python files pass Ruff.
- Markdown files pass markdownlint-cli2 under the project style configuration.
- The full unittest suite runs under coverage.
- Python changes must meet at least 97% changed-line coverage through
  diff-cover.
- The versioned hook can be installed with `git config core.hooksPath .githooks`.

Notes:

- Implemented on 2026-05-15. The gate is a coverage ratchet over changed Python
  lines, not a claim that the existing legacy global source coverage is already
  above 96%.

### LGB-041 - Modern Observer UI Surfaces

Status: Done

Priority: P1

Dependencies: LGB-039, LGB-040

Deliverables:

- Add `logbook watch-web` for a packaged web observer UI served on loopback.
- Build the web UI from React, Vite, Tailwind CSS, shadcn-style local
  components, and Lucide icons.
- Add `logbook watch --ui curses` for a full terminal-only dashboard using the
  Python standard-library curses runtime.
- Keep both UIs read-only over the observer snapshot contract.
- Add `mypy` to the quality gate and build the web UI as part of the hook.

Acceptance:

- The web UI starts from one Python command and polls `/observer/snapshot`.
- The web UI automatically chooses day or night appearance from local time.
- The curses UI shows health, current work, progress, ETA context, recent
  finished jobs, failures, and statistics in a compact resize-safe frame.
- Existing compact, full, JSON, once, and remote API watcher modes still work.
- The pre-commit hook runs Ruff, mypy, markdownlint, the web production build,
  unittest coverage, and more-than-96% changed-line coverage.

Notes:

- Implemented on 2026-05-16. The mypy gate starts as an incremental ratchet:
  legacy modules are scanned while the new watcher modules are enforced
  strictly; future work can narrow the legacy ignore surface as typing debt is
  paid down.

### LGB-042 - 1.2.0 Modern Observer Release

Status: Done

Priority: P1

Dependencies: LGB-041

Deliverables:

- Promote project package metadata, API metadata, web UI metadata, and release
  documentation to `1.2.0`.
- Add release documentation for the modern web and curses watcher surfaces.
- Keep the curses dashboard quit affordance explicit with `[q] quit` and tested
  `q`/Escape handling.
- Keep README, changelog, durable Codex context, and packaged web assets aligned
  with the released watcher behavior.

Acceptance:

- `scripts/quality-gate` passes with Ruff, mypy, markdownlint, web production
  build, unittest coverage, and more-than-96% changed-line coverage.
- `logbook watch --once --ui curses` displays the `[q] quit` footer in the
  rendered terminal frame.
- `logbook watch-web` serves packaged assets and a useful server-rendered
  snapshot fallback before JavaScript starts.

Notes:

- Prepared on 2026-05-17. This is a minor release because it adds new operator
  UI surfaces while preserving the observer snapshot contract and existing
  local-first pipeline behavior.
