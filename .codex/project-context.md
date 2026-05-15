# Project Context

## Product

Logbook is a local-first voice ingestion system for turning Sony ICD-PX370 recordings into structured Obsidian Markdown.

The system ingests audio from a Sony recorder connected to a Mac Mini, submits compute-heavy transcription and diarization work to `odin`, classifies transcripts by spoken prefix, and writes recoverable Markdown notes into an Obsidian vault.

## Primary Requirements

- Automatically detect and copy new recordings from Sony ICD-PX370.
- Preserve original audio during processing, avoid duplicate processing, and clean up source audio only after the 24-hour retention gate.
- Transcribe locally through `odin` using faster-whisper.
- Classify recordings by deterministic spoken prefixes.
- Stage log entries in `10 - Logs/00 - Inbox` before final consolidation.
- Render one canonical daily log per date under `06 - Timestamps/YYYY/MM-Month/`.
- Route category voice notes to `20 - Notes/00 - Inbox`.
- Route meetings to `30 - Meetings` with speaker labels.
- Route unknown prefixes to `99 - Dead Letters` with 28-day retention.
- Expose a narrow OpenClaw-facing API for health, status, rescue, reprocess, and rebuild actions.
- Expose proof-carrying memory queries through bounded read-only `/memory/*` API endpoints backed by local ledger/artifact state.
- Provide an independent read-only observer/watch surface for current pipeline
  progress, recent completions, failures, durations, and compact operational
  statistics.

## Non-Negotiable Invariants

- One date equals one final daily log file.
- Log entries are never written directly into final daily logs at ingestion time.
- Late arrivals rebuild the canonical daily log in timestamp order.
- OpenClaw observes and requests bounded actions; it does not receive arbitrary shell or deletion authority.
- OpenClaw gateway and node services must not run as `bernd`; the runtime owner is `clawdbot`.

## Suggested Architecture

- Mac Mini ingestion daemon:
  - launchd-triggered bounded recorder processing through `process-mounted-recorder`.
  - Recorder validation.
  - Copy/dedupe by checksum and source metadata.
  - SQLite ledger for job state and idempotency.
  - Router and Obsidian note writer.
  - Status API for OpenClaw.
  - Pipeline observer telemetry and compact watch CLI.

- `odin` GPU worker:
  - HTTP job API.
  - faster-whisper ASR.
  - pyannote diarization for meetings.
  - Health endpoint and explicit queued/offline behavior.

- Obsidian vault writer:
  - Pure path builders.
  - Markdown renderers with frontmatter.
  - Atomic file writes.
  - Delayed source-audio cleanup only after processing and vault sync are confirmed.

- Observer/watch layer:
  - Reads SQLite/API snapshots without mutating pipeline state.
  - Shows active run heartbeat, stage, progress, ETA, recent successes,
    failures, dead letters, and p50/p90 duration statistics.
  - Labels progress as measured, estimated, or unknown and keeps default output
    path-safe.

## Repo Practices

- Keep generated audio, logs, databases, model caches, and secrets out of git.
- Put implementation decisions in `.codex/decisions.md`.
- Keep backlog dependencies in `.codex/backlog.md` and memgraph.
- Keep live operational failure modes and prevention rules in `lessons-learned.md`.
- Use test fixtures for synthetic transcripts and tiny sample audio; do not commit personal recordings.
- Install `.githooks/pre-commit` with `git config core.hooksPath .githooks`;
  run `scripts/quality-gate` before release commits.
