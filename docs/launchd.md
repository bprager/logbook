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
- `local.logbook.recorder.mount-probe`: uses `StartOnMount` and opens the generated `LogbookMountRunner.app`, which runs `logbook process-mounted-recorder --env .env`. It copies discovered audio into the local inbox, transcribes through `odin`, diarizes meetings, routes generated notes into Obsidian, consolidates routed log entries into canonical daily logs, marks pushed vault artifacts as synced, and never deletes recorder or local audio. Recorder discovery/copy is retried briefly to tolerate removable-volume readiness and permission timing; if recorder access still fails, already-local copied/transcribed/routed work is allowed to finish before the command exits nonzero. Memgraph sync is bounded per job so graph latency does not hold the mount processor open indefinitely.
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

The mount probe is bounded but mutating: it may copy source audio, write the
SQLite ledger, submit work to `odin`, and write generated Obsidian notes. If the
recorder is not mounted or macOS denies access to the removable volume, it retries
briefly, finishes any already-local pending processing that can still proceed, then
reports `operational=no` or `copy_failed_count=1` and exits nonzero without deleting
anything. The retention audit is read-only and must print
`delete_audio=no` and `delete_recorder_audio=no`.

Do not manually `kickstart` `local.logbook.entity-linker` during a rollout check
unless vault mutation is explicitly intended. It is scheduled and runs with
`--execute`.

macOS removable-volume privacy:

- The mount processor is packaged as `$LOGBOOK_PROCESSING_ROOT/launchd/LogbookMountRunner.app`.
- Grant that app Full Disk Access, or at minimum removable-volume access when macOS prompts. Granting iTerm or VS Code is not enough because `launchd` runs a different privacy identity.
- After granting access, verify with `launchctl kickstart -k "gui/$(id -u)/local.logbook.recorder.mount-probe"` and confirm the mount log no longer reports `Operation not permitted`.

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
