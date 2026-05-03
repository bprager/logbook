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
- `local.logbook.retention-audit`: runs hourly at minute 17 and calls `logbook retention-status --env .env`. It reports retention configuration but does not delete audio.
- `local.logbook.entity-linker`: runs daily at 03:37 and calls `logbook link-daily-log-entities --env .env --months 3 --execute`. It scans canonical daily logs for existing people, event, and object notes, then adds Obsidian links without touching source audio.

Production note for `mimir`: `127.0.0.1:8787` is already used by the
`clawdbot` CashClaw/OpenClaw adapter, so the local Logbook API is configured on
`127.0.0.1:8788`.

Example user LaunchAgent install:

```bash
mkdir -p ~/Library/LaunchAgents
cp "$LOGBOOK_PROCESSING_ROOT/launchd/"*.plist ~/Library/LaunchAgents/

for label in \
  local.logbook.api \
  local.logbook.recorder.mount-probe \
  local.logbook.retention-audit \
  local.logbook.entity-linker
do
  launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/$label.plist" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/$label.plist"
done
```

Inspect or stop jobs:

```bash
launchctl print "gui/$(id -u)/local.logbook.api"
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/local.logbook.api.plist
```

Operational checks after bootstrap:

```bash
launchctl print "gui/$(id -u)/local.logbook.api"
launchctl print "gui/$(id -u)/local.logbook.recorder.mount-probe"
launchctl print "gui/$(id -u)/local.logbook.retention-audit"
launchctl print "gui/$(id -u)/local.logbook.entity-linker"

curl -fsS http://127.0.0.1:8788/health
curl -fsS http://127.0.0.1:8788/metrics | grep '^logbook_'

launchctl kickstart -k "gui/$(id -u)/local.logbook.recorder.mount-probe"
tail -n 40 "$LOGBOOK_PROCESSING_ROOT/logs/logbook-mount-probe.out.log"

launchctl kickstart -k "gui/$(id -u)/local.logbook.retention-audit"
tail -n 60 "$LOGBOOK_PROCESSING_ROOT/logs/logbook-retention-audit.out.log"
```

The mount probe is read-only. If the recorder is not mounted, it should report
`operational=no` and exit nonzero without creating, copying, routing, or deleting
anything. The retention audit is also read-only and must print
`delete_audio=no` and `delete_recorder_audio=no`.

Do not manually `kickstart` `local.logbook.entity-linker` during a rollout check
unless vault mutation is explicitly intended. It is scheduled and runs with
`--execute`.

Rollback all Logbook launchd jobs:

```bash
for label in \
  local.logbook.api \
  local.logbook.recorder.mount-probe \
  local.logbook.retention-audit \
  local.logbook.entity-linker
do
  launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/$label.plist" 2>/dev/null || true
done
```

Log files are under:

```text
$LOGBOOK_PROCESSING_ROOT/logs
```

Runtime guardrail:

- These plists are Logbook jobs only.
- Do not use them to start OpenClaw gateway or node services.
- OpenClaw runtime ownership on this host remains `clawdbot`; do not run OpenClaw services as `bernd`.
- Point OpenClaw at the loopback Logbook API with scoped read/action bearer tokens instead of granting shell access.
