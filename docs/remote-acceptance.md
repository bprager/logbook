# Remote ingestion acceptance record

Verified 2026-10-10 on `feature/remote-sony-ingestion`. Implementation covers
LGB-044 through LGB-047. Production version remains **1.2.3**.

## Automated evidence

`LOGBOOK_COVERAGE_COMPARE_REF=origin/main scripts/quality-gate` passed:

- Ruff and mypy checks.
- Markdown lint and existing observer web build.
- 237 tests, including 46 focused remote-ingestion tests.
- 99% whole-branch changed-line coverage, above the 97% required threshold.
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

## Physical acceptance progress

### First Mac automatic pilot activation — 2026-10-10 (Pacific)

The operator ran the short pilot installer on Tene-MacBook-Air and reported
successful bootstrap of both `local.logbook.remote-tunnel` and
`local.logbook.remote-agent`. The isolated receiver observed a new authenticated
status at 2026-10-11 01:21:41 UTC, following the earlier manual report. Pending
bytes and retries were zero, failure was null, and storage pressure was false.
The ledger remained at 25 recordings with no unfinished uploads. This verifies
initial managed-tunnel/signed-app execution, not yet network-change or reboot
recovery. Fresh recording/mount acceptance remains to be exercised.

### Fresh recording reconnect — 2026-10-10 (Pacific)

The operator safely disconnected Sony, made a new test recording and reconnected
it to Tene-MacBook-Air without running an ingestion command. The managed pilot
reported at 2026-10-11 01:29:39 UTC. The isolated ledger increased from 25 to 26
unique recordings and completed receipts, with no unfinished uploads. The new
recording was 426,653 bytes; server-side verification checked sizes and SHA-256
for all 26 files and SQLite integrity passed. Device pending bytes/retries were
zero, failure null and storage pressure false. All audio remained in the isolated
pilot at copied state. This proves automatic reconnect ingestion through the
signed app and managed SSH tunnel; it does not distinguish the mount trigger
from the independent minute schedule.

### Automatic SSH lifecycle — 2026-10-10

Optional SSH configuration and a separately generated tunnel LaunchAgent are
implemented. Native OpenSSH inspection verifies the hardened configuration is
passed to ProxyJump children and applies noninteractive authentication, strict
host keys, connection timeouts and foreground operation on both hops. Independent
review found the initial missing jump-hop inheritance; it is fixed and covered.

An isolated launchd job on mimir used the actual generated command/config to
forward traffic through SSH to fenrir's SSH port. The test terminated only its
own SSH process, verified launchd started a new process and forwarding recovered,
then unloaded the test job. No production jobs changed. Full tests cover existing
offline buffering/restart recovery, and the quality gate passed 237 tests.

This is not physical MacBook logout, Wi-Fi change or wake-from-sleep acceptance.
Those checks and signed-app delivery through the managed jump route remain the
next pilot steps. Package generation does not activate production or pilot jobs.

### First traveling MacBook pilot — 2026-10-10 (Pacific)

Tene-MacBook-Air installed the agent, created a persistent local signing identity,
and successfully generated the signed app after the packaging fixes above. The
operator opened that app with the Sony connected and reported 25 buffered MP3s.
The verified external USB volume UUID is
`9A37DA05-166E-3EAE-898C-C9D1CE0EBBD3`, recording folder is
`REC_FILE/FOLDER01`, and recorder timezone is `America/Los_Angeles`.

Using the existing key-authenticated `jump-mimir` SSH route through fenrir, the
operator then ran a separate pilot agent buffer through a manually opened SSH
tunnel to an isolated loopback receiver on mimir. The receiver used a separate
ledger and intake directory, not production. The agent reported zero pending
bytes, zero retries, no failure and no storage pressure.

Read-only server verification confirmed 25 unique recording hashes, 25 completed
upload receipts, zero unfinished uploads, and 121,418,203 received bytes. Every
stored file's size and SHA-256 matched the ledger; SQLite integrity was `ok`.
All jobs remained `copied`: no transcription or vault publication was run.
The temporary receiver was stopped after verification. Pilot files and ledgers
were retained; neither Sony recordings nor the original Mac buffer were removed.

The test used separate buffers and port 18791 so pilot receipts cannot mark the
original Mac buffer delivered to production. Repeated live upload, automatic SSH
startup/reconnection, signed-app transfer, login/reboot recovery and second-Mac
acceptance remain unverified. No VPN was used or installed.

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

The first Mac's signed-app recorder read and manual SSH transfer are evidenced
above. Automatic mount events, tunnel lifecycle and reboot/login recovery still
need verification, along with the complete setup on the second MacBook Air.
Actual mimir/Odin/vault/graph processing and guarded pruning must be approved and
field-tested with operator-chosen recordings. No such deployment or destructive
field test was performed during implementation.

Follow the checklist in [installation and recovery](remote-installation.md).
Before activation, explicitly decide the independent audio-backup and Mac spool
retention policies. Acknowledged Mac snapshots are currently retained; existing
saga backups still exclude audio. No v1.3.0 version promotion or tag was created.
