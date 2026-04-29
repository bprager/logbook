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

### Fixed

- Prevented recorder rediscovery from downgrading already consolidated jobs when the copied audio is still present and checksum-matched.

## [0.1.0] - 2026-04-27

### Added

- Initialized Codex project context, research notes, decision log, and dependency-aware backlog.
- Added repository guardrails for local-first voice ingestion, Obsidian log generation, and OpenClaw runtime ownership.
- Added planning status tracking and an architecture plan based on the `prager.ws` home-network inventory.
- Recorded decisions for `mimir` host placement, scoped bearer-token auth, Obsidian CLI vault access, and 24-hour source-audio cleanup.
- Added a polished GitHub README and machine-readable `VERSION` file for the first minor planning release.
