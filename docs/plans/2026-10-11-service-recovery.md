# Persistent Logbook services and bounded recovery

The operator authorized production upload restoration, unattended operation,
bounded service recovery, and alarms for continuing failures on October 11.

## Deployment and policy

- Preserve the enrolled Mac's loopback port 18791 and device credentials; receive
  directly into the backed-up production ledger and retained remote inbox.
- Run a boot-time macOS LaunchDaemon as `bernd`, supervising the receiver,
  existing status API, and periodic queue processor. No GUI login dependency.
  The supervisor owns its child processes; launchd cleans its process group if
  the supervisor exits. Never run OpenClaw services as `bernd`.
- Probe once per minute. Allow startup grace, then three restart attempts per
  incident, spaced at least one minute apart. Persist counts across supervisor
  restarts. After exhaustion, retain the alarm and stop restarting that service.
  Ten minutes of continuous health resets the incident budget. Manual reset is
  explicit. Queue processing failure is subject to the same budget.
- Probe Odin and use only its fixed existing systemd unit for bounded remote
  recovery. Loss of connectivity must not cause unbounded SSH or service retries.
- Keep the observer read-only; expose service status, restart count, exhausted
  budget and supervisor heartbeat in observer snapshots, curses, and metrics.
- Export through the existing node exporter textfile collector, leaving the
  unauthenticated local recording API on loopback.
- Integrate Prometheus/Grafana alert evaluation and the existing email route.
  Alert on exhausted recovery, sustained service failure, stale supervisor,
  host metrics scrape failure, and stalled unpublished remote work. Monitor from Odin
  so a failed mimir host cannot suppress its own alarm.
- Record transitions locally. Retain all audio. Do not tag a release or enable
  retention deletion.

## Finishing checks

Tests prove restart limits, persistence, healthy reset, manual reset, grace,
worker scheduling and failure, safe process cleanup, observer/metrics visibility,
and alarm rules. Live checks verify the real pending upload, transcription,
Obsidian publication and vault push; a controlled service stop recovers; a
synthetic broken service exhausts exactly three retries; alert firing, delivery
and resolution are verified through the configured channel. Commit and push
code and deployment definitions, recording evidence and genuine limitations.
