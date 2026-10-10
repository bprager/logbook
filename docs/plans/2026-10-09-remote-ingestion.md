# Remote Sony ingestion implementation plan

Goal: Deliver LGB-044 through LGB-047 on a feature branch without deploying,
changing credentials, deleting recordings or promoting the version.

Architecture: A separate private ingest service commits content into the existing
SQLite job ledger. A Mac outbox persists complete snapshots before transfer. A
periodic central worker uses the existing processing stages independently of USB.

## Completion checklist

- [ ] LGB-044: Add additive upload/device schema, bounded resumable protocol,
  transactional central registration and durable idempotent receipts. Test old
  ledgers, competing clients, wrong offsets/digests and crash/ack recovery.
  Files: `remote_store.py`, `remote_api.py`, `remote_config.py`, `ledger.py`, tests.
- [ ] LGB-045: Add strict Sony validation, stable atomic snapshots, SQLite outbox,
  per-file persistent retries and resumable HTTP client. Render a stable signed
  app and mount/reboot/interval LaunchAgent without installing it. Test offline,
  changed files, wrong volumes, symlinks, permissions and disk exhaustion.
  Files: `remote_agent.py`, `remote_launchd.py`, `remote_cli.py`, tests.
- [ ] LGB-046: Add mount-independent processing with a shared process lock,
  preserve known/pruned jobs, and safely associate remote jobs with later local
  recorder discovery. Test processing, late arrivals and retention gates.
  Files: `cli.py`, `copying.py`, `retention.py`, tests.
- [ ] LGB-047: Add redacted observer status, device authentication/rate audit,
  storage pressure and client retry reports. Complete templates, installation,
  recovery and physical acceptance checklist. Run the full quality gate against
  the feature branch base, not merely the latest incremental commit.

## Execution and verification

For each increment: write regression tests, run them to demonstrate the missing
behavior, implement, rerun focused tests, update backlog/changelog, then commit.
Use `.venv/bin/python -m unittest discover -s tests -p 'test_remote*.py'` for
focused verification. Final gate: `LOGBOOK_COVERAGE_COMPARE_REF=origin/main
scripts/quality-gate`. Inspect source paths and cleanup call boundaries explicitly.
Commit tested increments and push the feature branch. Keep actual two-Mac/VPN/TCC
field acceptance pending approval; tests use temporary synthetic recordings.

## Review findings and decisions

- Existing `recording_jobs.checksum_sha256` is already UNIQUE. Preserve it as the
  authority; do not derive presence from retained audio files.
- Upload bytes must be flushed before acknowledgment; register after atomic rename.
  Retrying completion recovers a rename that preceded a failed database commit.
- Remote metadata contains a basename and recording time/zone, never a client path.
- Preserve originals on Sony and retain acknowledged Mac spools for now. Production
  approval must choose spool retention and independent audio-backup policy.
- Direct copying currently can regress known/pruned jobs to copied; cover and fix.
- Existing runner can be reused as a stable parent process for macOS TCC attribution.
