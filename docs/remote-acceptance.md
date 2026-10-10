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

## Installation follow-up — 2026-10-10

The first MacBook installation exposed duplicate observer assets in the wheel
definition. Reproduced with Python 3.11 and fixed by removing redundant forced
inclusion. A regression test builds the actual wheel and checks every observer
asset, the remote CLI module and its entry point. The full quality gate now
passes 234 tests. A clean temporary Python 3.14 environment on mimir successfully
installed the package, ran both CLI help commands, imported the agent/API and
found the installed observer page. This is installation evidence, not a full
Python 3.14 test-suite or Intel MacBook runtime qualification.

## Signing follow-up — 2026-10-10

The MacBook's persistent certificate was valid, but app signing failed because
the generated C source was stored in `Contents/MacOS`. Reproduced the exact
failure with real macOS `codesign`. The builder now stores source in Resources
and removes its obsolete generated source from MacOS when rebuilding an old
package. Real ad-hoc signing and strict verification pass for fresh and repaired
temporary apps. This test needs no keychain changes; production package creation
still requires a persistent certificate. MacBook certificate signing and TCC
acceptance must still be verified on the physical machine.

## Independent review

A read-only review found and reproduced duplicate-session capacity leakage and
idle downstream retry gaps. Regression tests were added and both were fixed.
Follow-up review caught a crash window while reclaiming competing upload parts;
registration and all receipts now commit before those parts are reclaimed. The
reviewer reran focused recovery tests and confirmed that fix.

## Pending physical acceptance

### Isolated network pilot — 2026-10-10

A follow-up pilot on mimir used two separate agent processes, separate temporary
credentials and buffers, and a temporary receiver bound only to loopback.
Both agents buffered the same synthetic recording while the receiver was offline.
After starting the receiver, fresh agent processes delivered both buffers over
real HTTP. Checks confirmed one central job, identical source/destination SHA-256,
SQLite integrity, an unchanged synthetic source, and both acknowledged buffers
retained. The receiver was stopped afterward. All data was in a temporary
directory; no production services, credentials or data were changed.

Only recorder-volume validation was simulated. This pilot does not establish
physical USB, VPN, signing, TCC, login or reboot acceptance. At preflight, this
session was on mimir and `/Volumes` contained only Macintosh HD and Recovery.
The two MacBook hostnames and physical recorder access are still needed.

### Remaining operator checks

Actual certificate signing, macOS TCC grants, USB mount events, VPN behavior,
and reboot/login recovery must be checked on **both physical MacBook Airs**.
Actual mimir/Odin/vault/graph processing and guarded pruning must be approved and
field-tested with operator-chosen recordings. No such deployment or destructive
field test was performed during implementation.

Follow the checklist in [installation and recovery](remote-installation.md).
Before activation, explicitly decide the independent audio-backup and Mac spool
retention policies. Acknowledged Mac snapshots are currently retained; existing
saga backups still exclude audio. No v1.3.0 version promotion or tag was created.
