# Logbook

Local-first voice capture for an Obsidian daily log, voice-note archive, and meeting transcript system.

Logbook turns recordings from a Sony ICD-PX370 into structured Markdown. It stages daily log entries safely, transcribes audio on the local GPU host `odin`, writes canonical notes into an Obsidian vault, and exposes a narrow OpenClaw API for status and approved recovery actions.

> Status: implementation has started after planning release `0.1.0`. The safe local ingest, fake `odin` boundary, and test-vault routing slices are working.

## What It Does

- Ingests recordings when the Sony recorder is connected to `mimir`.
- Copies and deduplicates audio through a recoverable SQLite job ledger.
- Sends transcription and diarization jobs to `odin`.
- Routes recordings by spoken prefix: logs, meetings, ideas, tasks, research, reminders, and dead letters.
- Stages log entries before rendering one canonical daily log per date.
- Updates the GitHub-backed Obsidian vault through the configured Obsidian CLI workflow.
- Lets OpenClaw observe queue health, dead letters, inbox status, and bounded repair actions.
- Deletes local and recorder-side source audio after the 24-hour retention gate confirms processing and vault sync.

## Architecture

```text
Sony ICD-PX370
  -> mimir launchd trigger
  -> mimir ingest daemon + SQLite ledger
  -> odin GPU worker for ASR and diarization
  -> Obsidian CLI updates to bprager/obs-vault
  -> OpenClaw on mimir via loopback status/action API
  -> saga for backup/archive
```

Host roles:

| Host | Role |
| --- | --- |
| `mimir` | Mac Mini recorder host, OpenClaw host, local ingest state |
| `odin` | GPU worker and observability host |
| `saga` | Backup/archive target |
| `fenrir` | Public edge for dashboards only |
| `qnap` | Legacy storage; no new runtime dependency |

## Core Invariants

- One date has exactly one final daily log.
- Log entries are staged in `10 - Logs/00 - Inbox` before final rendering.
- Final daily logs live under `06 - Timestamps/YYYY/MM-Month/`.
- Late arrivals rebuild the canonical daily log instead of creating variants.
- OpenClaw reads broadly but acts only through named, audited endpoints.
- Raw audio is never linked from generated Obsidian notes.

## Repository Map

- [docs/PRD.md](docs/PRD.md) - product requirements.
- [AGENTS.md](AGENTS.md) - agent and runtime guardrails.
- [.codex/architecture-plan.md](.codex/architecture-plan.md) - host placement, protocols, and implementation strategy.
- [.codex/backlog.md](.codex/backlog.md) - dependency-aware delivery backlog.
- [.codex/decisions.md](.codex/decisions.md) - accepted decisions and open questions.
- [.codex/status.md](.codex/status.md) - current planning status.
- [Changelog.md](Changelog.md) - release history.

## Planned MVP

1. Define configuration, schemas, and API contracts.
2. Build the local routing and Markdown rendering slice with fixture transcripts.
3. Add the `odin` worker contract and real faster-whisper integration.
4. Add Sony recorder mount detection and deduplicated copying.
5. Add Obsidian CLI sync/write workflow.
6. Add OpenClaw status/action endpoints and Prometheus metrics.
7. Run an end-to-end pilot against a test vault before writing to the real vault.

## Configuration

Secrets and host-local settings live in `.env`, which is intentionally ignored by git. The local placeholder includes:

- `ODIN_API_TOKEN`
- `LOGBOOK_READ_TOKEN`
- `LOGBOOK_ACTION_TOKEN`
- `HUGGINGFACE_TOKEN`
- Obsidian CLI and vault paths
- Sony recorder mount details

Use [.env.example](.env.example) as the tracked, secret-free template.

## Development

Run the read-only recorder discovery dry run:

```bash
PYTHONPATH=src python3 -m logbook.cli recorder-discover --env .env
```

Run the checksum-based ingest dry run without writing to the ledger:

```bash
PYTHONPATH=src python3 -m logbook.cli ingest-dry-run --env .env
```

Record discovered checksums in the local SQLite ledger without copying or deleting audio:

```bash
PYTHONPATH=src python3 -m logbook.cli ingest-dry-run --env .env --record-discovery
```

Copy discovered recordings into the local processing inbox and verify checksums:

```bash
PYTHONPATH=src python3 -m logbook.cli copy-discovered --env .env
```

Exercise the `odin` client boundary with fake local transcripts:

```bash
PYTHONPATH=src python3 -m logbook.cli fake-transcribe-copied --env .env
```

Exercise meeting diarization for transcribed meeting-prefix jobs:

```bash
PYTHONPATH=src python3 -m logbook.cli diarize-meetings --env .env
```

Exercise the same flow with the fake `odin` client:

```bash
PYTHONPATH=src python3 -m logbook.cli fake-diarize-meetings --env .env
```

The diarization pass skips non-meeting transcripts, persists speaker-labelled JSON under
`LOGBOOK_PROCESSING_ROOT/diarization`, and records `diarized` jobs in SQLite for later meeting
note rendering.

Route transcribed jobs into an explicit test vault without touching the real Obsidian vault:

```bash
PYTHONPATH=src python3 -m logbook.cli route-transcripts --env .env --vault /Users/bernd/VoiceIngest/test-vault
```

Route exactly one ledger job:

```bash
PYTHONPATH=src python3 -m logbook.cli route-transcripts --env .env --vault /Users/bernd/VoiceIngest/test-vault --job-id 17
```

Validate the configured Obsidian CLI and vault path without writing files:

```bash
PYTHONPATH=src python3 -m logbook.cli vault-preflight --env .env
```

This preflight also checks that the configured vault name is registered with `obsidian-cli`.

Wrap routing with the Obsidian workflow after the CLI command templates are configured:

```bash
PYTHONPATH=src python3 -m logbook.cli route-transcripts --env .env --vault /Users/bernd/VoiceIngest/test-vault --vault-workflow obsidian
```

Write routed notes through `obsidian-cli create` after the target vault is registered in Obsidian:

```bash
PYTHONPATH=src python3 -m logbook.cli route-transcripts --env .env --vault /Users/bernd/Obsidian/obs-vault --writer obsidian-cli --job-id 17
```

Backfill jobs already marked as routed:

```bash
PYTHONPATH=src python3 -m logbook.cli route-transcripts --env .env --vault /Users/bernd/Obsidian/obs-vault --writer obsidian-cli --include-routed
```

Render canonical daily logs from routed inbox entries:

```bash
PYTHONPATH=src python3 -m logbook.cli consolidate-logs --env .env --vault /Users/bernd/Obsidian/obs-vault --writer obsidian-cli
```

Start the read-only FastAPI status API on the configured loopback host and port:

```bash
PYTHONPATH=src python3 -m logbook.cli serve-api --env .env
```

The interactive Swagger UI is available at `/docs`, ReDoc at `/redoc`, and the OpenAPI schema at
`/openapi.json`. If `LOGBOOK_READ_TOKEN` is set, use Swagger's **Authorize** control with a bearer
token before calling status endpoints.

Bounded action endpoints use `LOGBOOK_ACTION_TOKEN` and only record auditable intent in SQLite:

- `POST /jobs/{id}/reprocess`
- `POST /dead-letters/{id}/rescue`
- `POST /logs/{date}/rebuild`

Send an `idempotency_key` in the JSON body when OpenClaw may retry an action request.

Render launchd plists for the local API, mount probe, and retention audit:

```bash
PYTHONPATH=src python3 -m logbook.cli launchd-render --env .env
```

The mount-trigger plist uses `StartOnMount` but runs only the read-only `recorder-discover`
probe. It does not copy, transcribe, route, or delete audio. See
[docs/launchd.md](docs/launchd.md) for install guidance and the OpenClaw runtime ownership
guardrail.

Run the standard-library test suite:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## License

This project is released under [CC0 1.0 Universal](LICENSE).
