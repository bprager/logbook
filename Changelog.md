<!-- markdownlint-disable MD024 -->
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

### Fixed

- Prevented recorder rediscovery from downgrading already consolidated jobs when the copied audio is still present and checksum-matched.
- Fixed local inbox copying from the Sony recorder by copying audio bytes without preserving recorder filesystem flags that macOS may reject.
- Fixed the Obsidian vault stage-command template so generated `10 - Logs` paths with spaces can be staged by the vault workflow.
- Expanded the Obsidian vault stage-command template to include all generated Logbook note roots, including daily logs, reviews, meetings, category notes, and dead letters.

## [0.1.0] - 2026-04-27

### Added

- Initialized Codex project context, research notes, decision log, and dependency-aware backlog.
- Added repository guardrails for local-first voice ingestion, Obsidian log generation, and OpenClaw runtime ownership.
- Added planning status tracking and an architecture plan based on the `prager.ws` home-network inventory.
- Recorded decisions for `mimir` host placement, scoped bearer-token auth, Obsidian CLI vault access, and 24-hour source-audio cleanup.
- Added a polished GitHub README and machine-readable `VERSION` file for the first minor planning release.
