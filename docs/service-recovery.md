# Persistent service recovery

Logbook's production receiver, API and queue processor run under a boot-time
`local.logbook.supervisor` LaunchDaemon. The daemon and children run as `bernd`,
not root, and do not depend on a desktop login. The receiver keeps the enrolled
Mac's existing port 18791 and device registry, but uses the production ledger.
The old user-level API/receiver/queue jobs are disabled to prevent duplicate
listeners. OpenClaw ownership and runtime are unaffected.

## Recovery policy

GPU work must not block the worker's HTTP event loop. A busy worker still needs
to answer health checks; otherwise the supervisor can restart useful processing.
The October 21 repair runs transcription and speaker labeling in a thread while
an async lock preserves serial GPU work. Verify health during an actual long job,
not only while idle. The recorder helper now uses frozen revision `5fc327f`; the
supervisor's receiver/API/queue still use the validated `50de098` snapshot below.

The supervisor checks services once per minute. Receiver/API checks require an
HTTP health response; the receiver checks its ledger and credential registry.
A newly started process has 90 seconds to become ready. Startup grace is not
reported as confirmed readiness. The queue runs once per minute after a previous
successful run finishes, with a 30-minute maximum runtime. Its normal idle exit
is healthy. Failed runs consume the same recovery budget as failed services.

Each incident permits three restart attempts, at least 60 seconds apart. Attempts
are saved before starting the repair and survive supervisor restarts. After the
third attempt fails, the service remains stopped or unhealthy, with a latched
alarm. Ten minutes of continuous health replenishes the budget. Launchd keeps the
supervisor itself running with a 60-second crash throttle; external monitoring
alarms on a missing/stale supervisor rather than trusting its own health claim.

Odin is probed through its existing health endpoint. Repairs use a fixed SSH
command for `logbook-odin-worker.service`, with strict host-key checking,
noninteractive authentication and a connection timeout. The existing SSH alias
selects its identity; the configured worker address selects the destination.
Network failure must not trigger changes to firewall or macOS privacy settings.
Odin also limits native systemd restarts to three starts per 15 minutes, with
a one-minute delay. These local crash retries are separate from the supervisor's
three remote repair attempts; repeated failure remains bounded at both layers.

Runtime files live under `~/VoiceIngest/services`:

- `supervisor-state.json`: readiness, recovery attempts and exhausted budgets.
- `events.jsonl`: state transitions, dates and recovery actions.
- `logs/`: service output; child logs rotate on restart after 5 MiB.
- `metrics/logbook.prom`: atomically replaced, path-safe status metrics.
- `production-package/`: generated service settings and LaunchDaemon definition.

The watch screen and `/observer/snapshot` show recovery status. The API's existing
`/metrics` also includes these metrics. Mimir's existing node exporter reads the
textfile directory so Odin can monitor recovery even when the Logbook API fails.
The recording API remains on loopback; no unauthenticated recording routes are
opened to the LAN. All audio is retained; retention cleanup remains disabled.

## Alarms

Deployment definitions are in `deployment/monitoring`. Prometheus on Odin checks:

- Three recovery attempts exhausted.
- An essential service unhealthy for five minutes.
- Supervisor heartbeat older than three minutes, sustained for one minute.
- Mimir's metrics endpoint unreachable for three minutes.
- Missing supervision metrics for three minutes.
- An unreadable publication ledger for three minutes.
- A received recording unpublished for over 30 minutes, sustained for five minutes.

Grafana evaluates the firing alerts each minute and sends them through the
existing `home-email` route. The existing policy groups notifications, waits
30 seconds initially, and repeats ongoing incidents every 12 hours. Recovery
messages follow the existing route's settings. The Logbook Operations dashboard
shows health, attempts, exhausted budgets, heartbeat age and publication delay.

These external checks need Odin and its monitoring/mail services to be available.
An always-running service cannot guarantee recovery while the host is powered
off, its encrypted disk is locked, the network is unavailable, or access is denied.
Those conditions require their underlying problem to be corrected.

## Installation and maintenance

### Production source isolation

On October 21, the development checkout was on `main`, which did not contain
`logbook.supervisor`, `logbook.remote_cli`, or `process-queued`. The installed
supervisor still imported that checkout and repeatedly exited with
`No module named logbook.supervisor`. Its last saved queue budget was exhausted.

The deployed source is now a Git archive of commit
`50de0984acf3a389134ef34b108fae77dbb68348`, stored under
`~/VoiceIngest/services/releases/<commit>`. `DEPLOYED_COMMIT` records its revision.
Both the installed and generated supervisor plists use that directory as their
working directory and its `src` directory as `PYTHONPATH`. Children inherit it.
The onsite mount app also uses this source directory. Credentials, the existing
Python environment, device registry, ledger and recovery budgets remain external.
Do not remove the project's `.venv` or `.env`; those paths are still in use.

For an update, validate the selected revision first, export it into a new release
directory, and point the supervisor and mount helper at that directory. Preserve
the old source and configuration for rollback. Update both the installed and
generated supervisor plist; a later package generation must not silently point
production at the development checkout again. The helper's `--repo-root` must be
the release directory, while `--env` and `--python-bin` retain their absolute
production paths. Loading a new system plist requires administrator access.
Wait for the old supervisor to stop before bootstrapping its replacement.

After deployment, check fresh health responses, supervisor state and a completed
scheduled queue run. Confirm source isolation by switching the development
checkout to an older branch and verifying those checks again. On October 21,
receiver/API checks and scheduled queue processing succeeded with development
on `main`; all four supervised components were healthy with zero exhausted
budgets, no unfinished ledger jobs and no unpublished remote deliveries.
All 293 tests and the full quality gate passed. The Sony was disconnected, so
a fresh physical-recorder import remains unverified until it is reconnected.

Configuration backups are under `~/VoiceIngest/services/repair-20261021T181335Z`
and `mount-helper-20261021T181623Z`; verify actual directory names before rollback.
Restore the saved plist only after ensuring its referenced source is available.
Source audio was not deleted during this recovery.

### Onsite Sony mount helper

The existing `LogbookMountRunner.app` must use the same project Python environment
as the supervised services. A legacy helper compiled with Homebrew's global
Python can stop before emitting pipeline telemetry when a dependency is added.
Check its generated C source for the interpreter path; older bundles keep the
source under `Contents/MacOS`, newer bundles under `Contents/Resources`.
Back up the existing app before rebuilding it with the checkout's `.venv` Python:

```bash
.venv/bin/python -m logbook.cli launchd-render --env "$PWD/.env" \
  --repo-root "$LOGBOOK_RELEASE_ROOT" --python-bin "$PWD/.venv/bin/python"
launchctl kickstart "gui/$(id -u)/local.logbook.recorder.mount-probe"
.venv/bin/python -m logbook.cli watch --env .env --once
```

Rendering refreshes the generated mount app and generated plist files. Set
`LOGBOOK_RELEASE_ROOT` to the validated release directory above first. Rendering
does not install or load those plists. Keep the installed remote services, supervisor,
and retention settings intact. Verify the mount run reaches vault sync and a
second run skips the same recordings. Keep live service imports on the validated
release snapshot. A mutable development checkout is not a deployment directory.
After restoring the deployed code, reset only the affected queue budget using
the command below.

### Supervisor package

Generate the unprivileged daemon package from the validated release directory,
using the existing production Python and environment paths:

```bash
cd "$LOGBOOK_RELEASE_ROOT"
PYTHONPATH="$LOGBOOK_RELEASE_ROOT/src" \
  /Users/bernd/Projects/Logbook/.venv/bin/python -m logbook.service_install \
  --env /Users/bernd/Projects/Logbook/.env \
  --server-config ~/VoiceIngest/services/remote-server.json \
  --output ~/VoiceIngest/services/production-package --odin-host odin
```

Before the first migration, back up the production ledger with SQLite's backup
API. Preserve the device registry and validate that receiver and processor use
the same ledger. Stop and disable the previous user-level API, receiver and queue
jobs. Install the generated plist as root:wheel, mode 0644, in
`/Library/LaunchDaemons`, then bootstrap it into the system domain. Never run two
supervisors or leave the old user jobs enabled alongside this daemon.

Configure the existing node exporter with
`--collector.textfile.directory=/Users/bernd/VoiceIngest/services/metrics`.
Its existing privileged service can read this private directory. Keep the
supervisor and processing services unprivileged. Copy the monitoring definitions
into the existing Prometheus rules and Grafana provisioning directories, validate
with `promtool`, and reload provisioning. Do not add a LAN scrape of port 8788;
the existing `mimir-node` scrape on 9100 supplies the supervision metrics.

After fixing an exhausted service, explicitly reset only its budget:

```bash
.venv/bin/python -m logbook.supervisor \
  --config ~/VoiceIngest/services/production-package/supervisor.json --reset receiver
```

Other component names are `api`, `queue`, and `odin`. A reset is consumed on the
next one-minute check. Restarting the supervisor alone preserves the budgets.

Verify with the full quality gate and the Prometheus rule test file before
changing the policy. Confirm a real upload, transcription and published vault
commit separately: an upload receipt proves durable delivery, not publication.
