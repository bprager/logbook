# Logbook

Local-first voice capture for an Obsidian daily log, voice-note archive, and meeting transcript system.

Logbook turns recordings from a Sony ICD-PX370 into structured Markdown. It stages daily log entries safely, transcribes audio on the local GPU host `odin`, writes canonical notes into an Obsidian vault, and exposes a narrow OpenClaw API for status and approved recovery actions.

> Status: planning release `0.1.0`. The architecture, backlog, and operating guardrails are ready; implementation begins next.

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

## License

This project is released under [CC0 1.0 Universal](LICENSE).
