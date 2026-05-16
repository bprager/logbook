# Logbook

Local-first voice capture for an Obsidian daily log, voice-note archive, and meeting transcript system.

Logbook turns recordings from a Sony ICD-PX370 into structured Markdown. It stages daily log entries safely, transcribes audio on the local GPU host `odin`, writes canonical notes into the live Obsidian vault, and exposes a narrow OpenClaw API for status and approved recovery actions.

> Status: minor release `1.1.0` from the stable operational line. The live recorder-to-`odin`-to-Obsidian path is running on `mimir`, with mount-triggered bounded processing, observer/watch UI, meeting diarization, audited retention cleanup, Memgraph memory health, launchd jobs, Prometheus metrics, and current `saga` restore-drill evidence in place.

## What It Does

- Ingests recordings when the Sony recorder is connected to `mimir`.
- Copies and deduplicates audio through a recoverable SQLite job ledger.
- Sends transcription and diarization jobs to `odin`.
- Routes recordings by spoken prefix: logs, meetings, ideas, tasks, research, reminders, and dead letters.
- Stages log entries before rendering one canonical daily log per date.
- Updates the GitHub-backed Obsidian vault through the configured Obsidian CLI workflow.
- Lets OpenClaw observe queue health, dead letters, inbox status, and bounded repair actions.
- Plans a compact independent observer/watch view for active pipeline progress,
  recent completions, failures, ETAs, and path-safe statistics.
- Deletes local and recorder-side source audio after the 24-hour retention gate confirms processing and vault sync.
- Links canonical daily logs to existing Obsidian People, Event, and Object notes.
- Backs up restorable non-audio operational state to `saga`.

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
- [docs/metrics.md](docs/metrics.md) - Prometheus scrape targets, metrics, and alert candidates.
- [docs/pipeline-observer.md](docs/pipeline-observer.md) - design for the independent watch program and observer telemetry.
- [docs/releases/1.1.0.md](docs/releases/1.1.0.md) - release notes for the observer/watch UI and quality gate.
- [docs/backups.md](docs/backups.md) - `saga` backup policy and restore-drill runbook.
- [docs/releases/1.0.1.md](docs/releases/1.0.1.md) - patch release notes for mount-triggered ingest hardening.
- [docs/releases/1.0.0.md](docs/releases/1.0.0.md) - stable release notes and verification evidence.
- [docs/releases/0.2.0.md](docs/releases/0.2.0.md) - operational MVP release notes.
- [lessons-learned.md](lessons-learned.md) - durable operational lessons from live incidents and recoveries.
- [Changelog.md](Changelog.md) - release history.

## Operational Release

- `mimir` owns Sony recorder discovery, local SQLite state, Logbook launchd jobs, Obsidian writes, retention cleanup, and the loopback status/action API.
- `odin` runs the GPU ASR worker with faster-whisper `large-v3` on CUDA and exposes internal health and metrics.
- The live Obsidian vault receives inbox notes, canonical daily logs, dead-letter/rescue updates, entity links, and generated review artifacts through the configured Git workflow.
- OpenClaw gets bounded read/action API access without shell access to Logbook or `odin`.
- `saga` stores timestamped non-audio backup artifacts validated by restore drills.

## Configuration

Host-local runtime settings live in `.env`, which is intentionally ignored by git. Encrypted deployable settings live in tracked `secrets.yaml`, protected with SOPS/age; do not decrypt or print secret values in logs or release notes.

The local placeholder includes:

- `ODIN_API_TOKEN`
- `LOGBOOK_READ_TOKEN`
- `LOGBOOK_ACTION_TOKEN`
- `HUGGINGFACE_TOKEN`
- Obsidian CLI and vault paths
- Sony recorder mount details

Use [.env.example](.env.example) as the tracked, secret-free template.

## Development Quality Gate

Install the versioned pre-commit hook once per clone:

```bash
git config core.hooksPath .githooks
.venv/bin/python -m pip install -e '.[dev]'
```

Run the same gate manually before a release:

```bash
scripts/quality-gate
```

The gate runs Ruff over Python files, markdownlint-cli2 over every tracked
Markdown file, `mypy` over the Python package, the watcher web UI production
build, the full unittest suite under coverage, and `diff-cover` with a 97%
changed-line coverage threshold for Python changes. That threshold guarantees
more than 96% changed-line coverage. The full coverage report is still printed
so legacy coverage debt remains visible while new changes are ratcheted upward.

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

Meeting notes are rendered by the normal routing command after diarization. They are written under
`30 - Meetings/YYYY/MM-Month` and include participant placeholders, summary, decisions, action items,
and a speaker-labelled transcript.

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

Print a compact, read-only observer snapshot from the SQLite ledger:

```bash
PYTHONPATH=src python3 -m logbook.cli watch --env .env --once
PYTHONPATH=src python3 -m logbook.cli watch --env .env --once --json
PYTHONPATH=src python3 -m logbook.cli watch --env .env --theme auto
PYTHONPATH=src python3 -m logbook.cli watch --env .env --ui full
PYTHONPATH=src python3 -m logbook.cli watch --env .env --ui curses
PYTHONPATH=src python3 -m logbook.cli watch --api http://127.0.0.1:8788 --read-token-env LOGBOOK_READ_TOKEN
```

Start the modern web watcher UI on loopback:

```bash
PYTHONPATH=src python3 -m logbook.cli watch-web --env .env
```

The web watcher serves a compact React/Vite interface built with shadcn-style
components from `web/observer` and packaged static assets under
`src/logbook/static/watch`. It polls `/observer/snapshot`, shows active work,
progress, ETA confidence, recent outcomes, failures, and rolling statistics,
and automatically switches between day and night appearance from the local
computer time.

The matching API endpoint is `GET /observer/snapshot`. The observer reports
recent finished jobs, failures visible in durable state, dead letters, basic
duration statistics, and bounded Odin/Memgraph reachability checks. When
`process-mounted-recorder` is running, it reports the active run, heartbeat age,
stale status, current top-level stage, and ETA/progress estimate when enough
comparable stage-duration history exists. Copy, route, consolidation, and
vault-sync stages report measured progress when they know their byte or item
counts. The live terminal UI supports automatic day/night appearance,
`--theme day|night|auto`, `--no-color`, status filters, script-friendly
failure/stale exit policies, and optional full-screen terminal dashboards with
`--ui full` or `--ui curses`. In a live interactive terminal the dashboards
support `q`, `r`, `f`, `a`, and `+/-` key controls; the curses version also
supports `s` for successes and `d` for dead letters.

Inspect audio retention cleanup eligibility without deleting anything:

```bash
PYTHONPATH=src python3 -m logbook.cli cleanup-plan --env .env
```

Run eligible local copied-audio cleanup explicitly:

```bash
PYTHONPATH=src python3 -m logbook.cli cleanup-audio --env .env --execute
```

Recorder-side source deletion is a separate opt-in gate:

```bash
PYTHONPATH=src python3 -m logbook.cli cleanup-audio --env .env --execute --include-recorder
```

Cleanup requires finalized processing, derived note metadata, a recorded `vault_synced_at`, and the
24-hour retention window. The API exposes read-only cleanup status at `/cleanup/audio`.

Render launchd plists for the local API, mount probe, and retention audit:

```bash
PYTHONPATH=src python3 -m logbook.cli launchd-render --env .env
```

The mount-trigger plist uses `StartOnMount` and runs `process-mounted-recorder`, a bounded ingest
command that copies new recorder audio, transcribes through `odin`, diarizes meetings, routes
generated notes, marks pushed vault artifacts as synced, and syncs proof-graph evidence. It does not
delete local or recorder audio. See [docs/launchd.md](docs/launchd.md) for install guidance and the
OpenClaw runtime ownership guardrail.

Run the same bounded mount-processing command manually:

```bash
PYTHONPATH=src python3 -m logbook.cli process-mounted-recorder --env .env
```

Plan a non-audio backup and run a restore drill:

```bash
PYTHONPATH=src python3 -m logbook.cli backup-run --env .env --repo-root /Users/bernd/Projects/Logbook
PYTHONPATH=src python3 -m logbook.cli backup-restore-drill --env .env --backup BACKUP_DIR_OR_REMOTE
```

Backups use SQLite backup semantics and exclude live secrets plus raw/quarantined
audio. See [docs/backups.md](docs/backups.md).

Run the standard-library test suite:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## License

This project is released under [CC0 1.0 Universal](LICENSE).
