# Logbook Backups

LGB-032 backs up Logbook operational state to `saga` without raw-copying a live
WAL-mode SQLite database.

## Target

On `mimir`, `bernd` does not write directly to `saga` over SSH. The supported
target is the `saga` NFS mount exposed on `odin`:

```text
192.168.1.3:/mnt/saga/Napoleon/logbook-backups
```

The live `.env` config uses:

```text
LOGBOOK_BACKUP_ROOT=192.168.1.3:/mnt/saga/Napoleon/logbook-backups
LOGBOOK_BACKUP_SSH_IDENTITY_FILE=/Users/bernd/.ssh/id_rsa_odin
```

Avoid the bare `odin` SSH alias for this job; local name resolution can point it
at `prager.homeip.net` rather than the LAN host.

## Policy

Included:

- SQLite ledger copied with `sqlite3.Connection.backup`.
- `.env.example`.
- Rendered launchd plists.
- Generated transcript, diarization, and insight JSON/Markdown state.
- A `manifest.json` with expected ledger counts and artifact list.

Excluded:

- Live `.env`.
- Bearer tokens and other secrets.
- Recorder source audio.
- Local inbox audio.
- Quarantined/trash audio under `trash/local-audio`.

Quarantined audio is intentionally excluded. It is retention-managed source
material, not durable state. If an operator needs to preserve audio for an
investigation, that should be a separate explicit evidence-preservation action.

## Runbook

Dry run:

```bash
PYTHONPATH=src .venv/bin/python -m logbook.cli backup-run \
  --env .env \
  --repo-root /Users/bernd/Projects/Logbook
```

Execute:

```bash
PYTHONPATH=src .venv/bin/python -m logbook.cli backup-run \
  --env .env \
  --repo-root /Users/bernd/Projects/Logbook \
  --execute
```

Restore drill from a remote backup:

```bash
PYTHONPATH=src .venv/bin/python -m logbook.cli backup-restore-drill \
  --env .env \
  --backup 192.168.1.3:/mnt/saga/Napoleon/logbook-backups/logbook-backup-YYYYMMDDTHHMMSSZ
```

The restore drill copies the backup to a temporary local directory, opens the
ledger read-only, runs `PRAGMA integrity_check`, checks the schema migration
version, and compares the live row count against the manifest. It does not write
to production SQLite, Obsidian, source audio, recorder audio, or Memgraph.

## First Production Evidence

Completed on 2026-05-03:

```text
backup_id=logbook-backup-20260503T155634Z
remote_target=192.168.1.3:/mnt/saga/Napoleon/logbook-backups/logbook-backup-20260503T155634Z
ledger_job_count=34
action_audit_count=4
copied_artifact_count=42
restore_drill=status=ok integrity_check=ok schema_version=1 job_count=34
raw_audio_files=0
live_env_files=0
```
