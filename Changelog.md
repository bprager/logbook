<!-- markdownlint-disable MD024 -->
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Fixed the terminal watch dashboard so recently copied, transcribed, diarized,
  and inbox-written jobs appear in the recent finished section, and jobless
  pipeline failures show a run id instead of fake job `#0`.
- Changed the terminal watch dashboard's recent finished and failure rows to
  show readable date/time values instead of long elapsed durations or missing
  failure timestamps.
- Changed watch dashboard timestamps to render in the operator's local
  timezone instead of showing raw UTC snapshot values.
- Changed watch JSON/API snapshot timestamps to use the operator's local
  timezone while preserving Sony recorder timestamps as recorded local times.
- Made the Obsidian vault workflow recover clean fast-forward-only sync
  failures by merging `origin/main` before continuing generated-note writes.
- Excluded the local `.uv-cache` dependency cache from Markdown lint during
  the quality gate.
- Updated stale API and web-watch service version metadata to match the current
  project version.

## [1.2.3] - 2026-05-21

### Added

- Added the latest finished recording-job timestamp to the observer snapshot,
  Web watch header, and terminal watch dashboards.

### Changed

- Changed `scripts/watch` to launch the upgraded curses terminal dashboard by
  default instead of the compact text renderer.
- Changed terminal watch dashboard frames from primitive ASCII borders to
  Unicode box-drawing borders for xterm-compatible terminals.
- Changed the curses watch dashboard to fit the current terminal dimensions,
  preserve the right border column, and redraw immediately after terminal
  resize events.

### Fixed

- Added bounded retries around mounted-recorder transcription and diarization
  so transient odin network failures do not strand copied recordings.
- Made Obsidian CLI note writes verify the target Markdown file exists and fall
  back to atomic filesystem writes when the CLI returns before the file is
  visible to Git.
- Prevented historical vault-sync blockers from failing a mount ingestion run
  after the current run's generated jobs were successfully marked synced.

## [1.2.2] - 2026-05-21

### Added

- `logbook watch --ui curses` now shows local Sony recorder mount status and
  exposes a one-key `[e] eject` action only when the recorder is mounted and no
  pipeline run is active.
- Added tracked operator scripts for `scripts/eject-voice-recorder` and
  `scripts/watch`.

## [1.2.1] - 2026-05-20

### Added

- Added `.codex/lessons-learned.md` for durable Codex workflow, debugging, and
  collaboration lessons separate from the production incident log.

### Changed

- Changed source-audio cleanup to the approved one-week retention gate and made
  the hourly retention LaunchAgent run guarded local and recorder cleanup.

### Fixed

- `logbook watch` now reports recent failed pipeline runs in the failures panel
  and failed count, even when the affected recording jobs later recover.

## [1.2.0] - 2026-05-17

### Added

- Added a modern `logbook watch-web` observer UI served from FastAPI with a
  packaged React/Vite frontend, shadcn-style components, Lucide icons, compact
  progress/stat panels, and automatic day/night appearance from local time.
- Added `logbook watch --ui curses` for a full terminal-only observer dashboard
  with resize-safe panels, progress bars, filters, and live key controls.
- Added `mypy` to the quality gate and raised the changed-line coverage ratchet
  to 97%, guaranteeing more than 96% coverage for changed Python lines.

### Fixed

- Dead-letter assignment to the log route now removes the obsolete generated
  dead-letter note after the replacement log inbox note is written and audited.
- The curses watcher now displays an explicit `[q] quit` control hint and uses
  the same tested quit-key path for `q` and Escape.

### Changed

- Promoted project metadata, API metadata, web UI package metadata, release
  documentation, and packaged watcher assets to `1.2.0`.

## [1.1.0] - 2026-05-15

### Fixed

- Launchd packaging now runs the Sony recorder mount processor through a stable `LogbookMountRunner.app` bundle instead of bare Python, giving macOS privacy/TCC a durable app identity for removable-volume access.
- The deterministic log classifier now accepts `a log entry` as a log-entry trigger phrase and strips the full trigger from generated log content.
- `process-mounted-recorder` now consolidates routed log inbox entries into canonical daily logs before vault-sync marking, preventing log jobs from remaining stuck at `inbox_written`.
- `process-mounted-recorder` now continues local downstream recovery when recorder copy/discovery fails, so already copied, transcribed, or routed jobs can still be finalized even if launchd cannot read the removable volume.
- Vault-sync proof checks now block previously marked jobs if required generated paths are missing from pushed vault `HEAD`, preventing stale `vault_synced_at` evidence from hiding missing notes.
- `process-mounted-recorder` now publishes pending generated vault files before vault-sync marking, recovering final ledger jobs whose notes exist locally but are not yet committed or pushed.
- Fixed `ingest-dry-run` so recorder access failures are reported as controlled warnings instead of crashing on a missing result field.
- Added bounded retry around mounted-recorder copy/discovery so launchd can survive transient removable-volume readiness or access errors.
- Extended mounted-recorder retry coverage for slow Sony volume readiness before launchd gives up on the only mount-triggered run.
- Made non-server CLI commands importable without FastAPI so operator recovery commands can run from lighter Python environments.
- Bounded per-job Memgraph sync from `process-mounted-recorder` so ledger and vault success cannot hang indefinitely behind graph latency.
- Ensured proof-graph sync creates Logbook label `id` indexes, tolerates transient Memgraph index DDL storage locks, and scopes relationship health/repair checks to managed Logbook relationships.

### Added

- Documented the proposed LGB-039 independent pipeline observer/watch program, including read-only observer telemetry, compact CLI/API snapshot requirements, progress/ETA rules, and planned metrics.
- Implemented LGB-039 Phase 1 with a ledger-derived `GET /observer/snapshot`
  endpoint and compact `logbook watch --once` / `--json` output for recent
  outcomes, durable failures, dead letters, and duration statistics.
- Implemented LGB-039 Phase 2 with SQLite pipeline run/stage telemetry,
  mounted-recorder stage instrumentation, stale-run detection, background
  heartbeats for long stages, and active-run/stage fields in the observer
  snapshot.
- Implemented LGB-039 Phase 3 with materialized stage-duration history and
  estimated active-stage progress/ETA using p50 duration, p90 risk duration,
  sample count, confidence, and collecting-baseline output for sparse history.
- Implemented LGB-039 Phase 4 with measured progress events, chunked byte
  progress for recorder copy, and count-based mounted-recorder progress for
  route, consolidation, and vault-sync stages.
- Implemented LGB-039 Phase 5 with live terminal refresh, remote API snapshots,
  bearer-token lookup, status filters, failure/stale exit policies, automatic
  day/night appearance, theme overrides, JSON mode, no-color fallback, and an
  optional full terminal dashboard with live key controls.
- Added bounded read-only Odin and Memgraph reachability probes to observer
  snapshots so the watch UI reports configured service availability.
- Added dry-run-first `manage-dead-letters --action rescue --target meeting` for reassigning a dead letter to the meeting pipeline, including diarization, meeting-note routing, audit logging, and obsolete dead-letter note removal after success.
- Added a versioned pre-commit quality gate under `.githooks/` that runs Ruff,
  markdownlint-cli2, the full unittest suite under coverage, and a 96% changed
  Python line coverage ratchet through diff-cover.
- Promoted project metadata to `1.1.0`.

## [1.0.1] - 2026-05-05

### Added

- Added `process-mounted-recorder` for bounded StartOnMount ingestion from Sony recorder through local copy, `odin` transcription/meeting diarization, Obsidian routing, vault-sync marking, and Memgraph sync.
- Added `lessons-learned.md` to capture operational incidents, recoveries, and prevention guidance.

### Changed

- Updated launchd mount handling to run the bounded ingest pipeline instead of the read-only recorder discovery command.
- Updated README, launchd runbook, Codex workspace docs, durable status, backlog, and release notes for the `1.0.1` patch release.

### Fixed

- Converted recorder folder permission denials into actionable nonzero CLI failures instead of Python stack traces.
- Increased the live `odin` HTTP timeout for long meeting recordings, normalized MP3 audio to WAV before pyannote diarization, and hardened the vault workflow against Obsidian workspace state and missing generated-note roots.
- Made the mounted-recorder processor recover a final, pushed, but not-yet-marked vault-sync state instead of skipping it when no routing candidates remain.

## [1.0.0] - 2026-05-05

### Added

- Added `1.0.0` release notes and promoted the live recorder-to-Obsidian system from operational MVP to stable operational release status.
- Added fresh `saga` backup and restore-drill evidence for the current 39-job ledger.

### Changed

- Updated project version metadata, README status, release documentation, backlog, and durable Codex status for the `1.0.0` release.

### Fixed

- Corrected post-release status metadata to reflect that `v0.2.0` is tagged and pushed.
- Added `pyannote.audio` 4.x speaker-diarization output support in the `odin` worker so live meeting jobs can receive speaker labels.
- Loaded the Hugging Face diarization token from `odin` env-file config as well as process environment variables.

## [0.2.0] - 2026-05-03

### Added

- Added a dependency-free Python CLI for read-only Sony recorder discovery.
- Added configuration parsing, recorder validation, Sony filename parsing, sidecar filtering, and unit tests.
- Added SQLite ledger initialization, SHA-256 checksumming, and checksum-based ingest dry-run planning.
- Added safe local copy from recorder to `VoiceIngest/inbox` with checksum verification and idempotent ledger updates.
- Added the `odin` worker contract/client boundary, fake `odin` client, transcript JSON persistence, and ledger transition to `transcribed`.
- Added a deployable FastAPI `odin` worker app with health, job submission, result retrieval, and lazy faster-whisper loading.
- Added client-side `odin-health` and real `transcribe-copied` commands for validating the configured HTTP `odin` worker.
- Validated the live `odin` ASR worker on `192.168.1.3` with an isolated real `transcribe-copied` run through CUDA-backed `large-v3`.
- Added deterministic prefix classification, Obsidian-style path builders, atomic Markdown rendering, and test-vault transcript routing.
- Added Obsidian CLI vault preflight and workflow wrapping with serialized writes, configurable command templates, and a tracked `.env.example`.
- Added an `obsidian-cli create` note writer option for routed Markdown after a target vault is registered in Obsidian.
- Tightened vault preflight to verify the configured vault name is registered with `obsidian-cli`.
- Added single-job routing with `--job-id` for controlled first writes into the real vault.
- Added `--include-routed` for controlled backfills and Git vault workflow templates for pull, stage, status, commit, and push.
- Added daily log consolidation into canonical `06 - Timestamps` paths with timestamp-sorted entries and ledger updates.
- Added late-arrival rebuild support for already consolidated daily logs, preserving the single canonical log path and timestamp order.
- Added category note routing into `20 - Notes/00 - Inbox/{category}` with prefix-stripped content and ledger tracking.
- Added dead-letter note routing into `99 - Dead Letters` with review status, 28-day `delete_after`, and rescue guidance.
- Added a read-only FastAPI status API with Swagger UI, OpenAPI metadata, optional bearer-token auth, and endpoints for health, jobs, log inbox, latest consolidated log, and dead letters.
- Added bounded FastAPI action endpoints for job reprocess, dead-letter rescue, and daily log rebuild requests with `LOGBOOK_ACTION_TOKEN` auth, SQLite audit records, and optional idempotency keys.
- Added launchd packaging that renders a keepalive Logbook API service, a read-only `StartOnMount` recorder probe, and an hourly no-delete retention audit job.
- Added meeting diarization support that resubmits meeting-prefix transcripts to `odin` with `diarize=true`, persists speaker-labelled JSON, and tracks diarization metadata in SQLite.
- Added speaker-labelled meeting note rendering under `30 - Meetings/YYYY/MM-Month` with participant, summary, decision, action-item, and transcript sections.
- Added audio retention cleanup planning, explicit local and recorder cleanup execution, SQLite cleanup audit fields, and read-only cleanup API status.
- Added guarded vault-sync marking that proves generated notes exist in pushed vault `HEAD` before setting ledger `vault_synced_at`.
- Added an optional generated Open Log Preview note with a clear non-canonical marker and CLI rendering command.
- Added deterministic summary and action candidate extraction into human-review artifacts without modifying canonical notes.
- Added high-priority roadmap/backlog entry for a proof-carrying Memgraph memory layer over Logbook evidence.
- Added LGB-027 proof-carrying memory graph sync/query support with dry-run-first CLI, optional Memgraph execution, and bounded FastAPI `/memory/*` read endpoints.
- Added LGB-028 action candidate resolution with durable SQLite review state, dry-run-first CLI, and token-protected FastAPI resolve endpoint.
- Added LGB-029 memory graph health/drift checks through CLI and FastAPI/OpenAPI.
- Completed LGB-022 end-to-end acceptance for the first production batch, including guarded local copied-audio cleanup, guarded recorder-side cleanup, post-cleanup recorder discovery, test/lint verification, and memory graph health verification.
- Processed the second production batch of 7 Sony recorder files through discovery, local copy, live `odin` transcription, Obsidian inbox routing, canonical daily log consolidation, pushed vault sync marking, and Memgraph proof-graph sync.
- Replaced the original placeholder/fake transcript vault entries with real `odin` transcripts from quarantined local audio, moved two non-log recordings to dead letters, rebuilt affected daily logs, and refreshed the Memgraph proof graph.
- Added the production hardening backlog for launchd rollout, Prometheus metrics, `saga` backups, memory graph pruning, and `0.2.0` release readiness.
- Added an entity-linking CLI and scheduled launchd job that links canonical daily logs to existing Obsidian people, event, and object notes.
- Added a dead-letter management wrapper and CLI for listing, assigning pending dead letters to log entries, rebuilding daily logs, rerunning entity linking, and auditing discards.
- Added LGB-033 memory graph repair with dry-run-first exact ID drift planning, missing proof-graph upserts, and explicit stale Logbook namespace pruning.
- Added LGB-031 Prometheus text metrics endpoints for the Logbook API and `odin` worker, plus internal scrape-target and alert-candidate documentation.
- Completed the LGB-030 production launchd rollout on `mimir`, installing Logbook API, recorder mount-probe, retention-audit, and entity-linker LaunchAgents while preserving OpenClaw ownership under `clawdbot`.
- Added LGB-032 `saga` backup and restore-drill commands using SQLite backup semantics, explicit non-audio artifact policy, remote copy support, and first production restore evidence.
- Added tracked SOPS/age encrypted `secrets.yaml` by explicit operator request.

### Fixed

- Prevented recorder rediscovery from downgrading already consolidated jobs when the copied audio is still present and checksum-matched.
- Fixed local inbox copying from the Sony recorder by copying audio bytes without preserving recorder filesystem flags that macOS may reject.
- Fixed the Obsidian vault stage-command template so generated `10 - Logs` paths with spaces can be staged by the vault workflow.
- Expanded the Obsidian vault stage-command template to include all generated Logbook note roots, including daily logs, reviews, meetings, category notes, and dead letters.
- Recognized ASR variants `Lock record` and `Block entry` as log-entry prefixes so rescued daily-log recordings do not remain as dead letters.
- Moved the local Logbook API to `127.0.0.1:8788` on `mimir` because `127.0.0.1:8787` is already occupied by the `clawdbot` CashClaw/OpenClaw adapter.

## [0.1.0] - 2026-04-27

### Added

- Initialized Codex project context, research notes, decision log, and dependency-aware backlog.
- Added repository guardrails for local-first voice ingestion, Obsidian log generation, and OpenClaw runtime ownership.
- Added planning status tracking and an architecture plan based on the `prager.ws` home-network inventory.
- Recorded decisions for `mimir` host placement, scoped bearer-token auth, Obsidian CLI vault access, and 24-hour source-audio cleanup.
- Added a polished GitHub README and machine-readable `VERSION` file for the first minor planning release.
