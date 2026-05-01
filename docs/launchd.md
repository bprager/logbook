# Logbook launchd Packaging

LGB-021 packages Logbook as local `launchd` jobs without loading them automatically.

Render host-local plists:

```bash
PYTHONPATH=src python3 -m logbook.cli launchd-render --env .env
```

By default, the generated plists are written under:

```text
$LOGBOOK_PROCESSING_ROOT/launchd
```

Generated jobs:

- `local.logbook.api`: runs `logbook serve-api --env .env`, keeps the FastAPI status/action API alive, and lets `launchd` send the normal termination signal on shutdown.
- `local.logbook.recorder.mount-probe`: uses `StartOnMount` and runs only `logbook recorder-discover --env .env`. It is intentionally read-only and does not copy, transcribe, route, or delete audio.
- `local.logbook.retention-audit`: runs hourly at minute 17 and calls `logbook retention-status --env .env`. It reports retention configuration but does not delete audio until LGB-026 implements the cleanup gate.
- `local.logbook.entity-linker`: runs daily at 03:37 and calls `logbook link-daily-log-entities --env .env --months 3 --execute`. It scans canonical daily logs for existing people, event, and object notes, then adds Obsidian links without touching source audio.

Example user LaunchAgent install:

```bash
mkdir -p ~/Library/LaunchAgents
cp "$LOGBOOK_PROCESSING_ROOT/launchd/"*.plist ~/Library/LaunchAgents/
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/local.logbook.api.plist
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/local.logbook.recorder.mount-probe.plist
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/local.logbook.retention-audit.plist
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/local.logbook.entity-linker.plist
```

Inspect or stop jobs:

```bash
launchctl print "gui/$(id -u)/local.logbook.api"
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/local.logbook.api.plist
```

Runtime guardrail:

- These plists are Logbook jobs only.
- Do not use them to start OpenClaw gateway or node services.
- OpenClaw runtime ownership on this host remains `clawdbot`; do not run OpenClaw services as `bernd`.
- Point OpenClaw at the loopback Logbook API with scoped read/action bearer tokens instead of granting shell access.
