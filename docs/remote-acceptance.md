# Remote ingestion acceptance record

Verified 2026-10-10 on `feature/remote-sony-ingestion`. Implementation covers
LGB-044 through LGB-047. Production version remains **1.2.3**.

## Automated evidence

`LOGBOOK_COVERAGE_COMPARE_REF=origin/main scripts/quality-gate` passed:

- Ruff and mypy checks.
- Markdown lint and existing observer web build.
- 233 tests, including 43 focused remote-ingestion tests.
- 98% whole-branch changed-line coverage, above the 97% required threshold.
- Existing direct USB, consolidation, vault synchronization, retention and backup
  regression suites remain passing.

| Capability | Evidence |
| --- | --- |
| One central job per content hash | Competing clients, repeat receipts, old/pruned ledger migration |
| Durable receipt recovery | Rename-before-registration interruption and post-commit receipt loss |
| Independent device recovery | Competing partial uploader reconciles after other uploader crashes |
| Persistent Mac buffering | Offline retry, restart, unplug simulation, orphan snapshot recovery |
| Bounded secure transfer | Per-device auth/revocation, offsets/digests, payload/session/rate limits, body deadlines |
| Read-only recorder access | Wrong volume, symlinks, permission denial, unstable files, low disk space |
| Existing downstream processing | Synthetic remote delivery, canonical late-arrival rebuild, Odin outage recovery |
| Independent final sync retries | Idle-worker vault retry and persistent delayed graph retry |
| Guarded retention | Receipt cannot prune; UUID/path/checksum and completion/sync/age gates enforced |
| Safe observability | Redacted API/JSON/plain observer status; old-ledger observation remains read-only |
| macOS package generation | Native launcher compiled; plists and signing invocations inspected in tests |

All recordings, credentials and ledgers used by these tests are temporary,
synthetic fixtures. Odin behavior is simulated at the existing client boundary;
no production vault or Memgraph content pipeline was exercised.

## Independent review

A read-only review found and reproduced duplicate-session capacity leakage and
idle downstream retry gaps. Regression tests were added and both were fixed.
Follow-up review caught a crash window while reclaiming competing upload parts;
registration and all receipts now commit before those parts are reclaimed. The
reviewer reran focused recovery tests and confirmed that fix.

## Pending physical acceptance

Actual certificate signing, macOS TCC grants, USB mount events, VPN behavior,
and reboot/login recovery must be checked on **both physical MacBook Airs**.
Actual mimir/Odin/vault/graph processing and guarded pruning must be approved and
field-tested with operator-chosen recordings. No such deployment or destructive
field test was performed during implementation.

Follow the checklist in [installation and recovery](remote-installation.md).
Before activation, explicitly decide the independent audio-backup and Mac spool
retention policies. Acknowledged Mac snapshots are currently retained; existing
saga backups still exclude audio. No v1.3.0 version promotion or tag was created.
