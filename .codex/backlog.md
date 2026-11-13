# Backlog

## Current — exact Docker removal race identified and corrected

PR98/cbdabdc merged5f2556d; publisher43432774530 passed and automatic delivery
returned RELEASE_READY5f2556d (sequence17). Actual diagnostics show the sole
reported consumer is this exact temporary recovery container, status removing,
Running=false. Database startup and retained batch verification already pass;
Docker stop returns before asynchronous --rm releases its mounts.

Branch fix/wait-for-owned-recovery-removal waits at most10 one-second intervals
only for the exact owned container in removing/false state, then still requires
an empty volume consumer list. Other/active consumers refuse immediately. If
removal wins the ps/inspect race, only a fresh empty consumer list permits success;
inspection/daemon errors never imply absence. Tests cover disappearance, exact
removal wait, other/active refusal and the bounded deadline. No policy, permissions,
resources, receipt or startup-gate changes. Exact next: required gate/review,
signed automatic delivery and pending recovery; confirm real receipt, guarded
restart and normal source-holder release before continuing reconciliation. No
owner interaction is required;132 prior windows/current final outcome unconfirmed.

## Current — batch verified; post-stop volume check still refuses recovery

PR97/f45aa4e merged9c5134b; publisher43431670801 passed, machine-only delivery
returned RELEASE_READY9c5134b. Under the proven18GiB/2CPU restore capacity the
worker reached its retained verification result, then the post-stop exclusive
volume check failed: fixture volume remains in use. No offline receipt or main
restart was permitted. Do not bypass this check or stop an unknown consumer.

Current branch fix/offline-volume-release-check adds bounded read-only consumer
name/state diagnostics on that exact refusal, at most3 containers/2000 chars each.
Regression first reproduced missing diagnostics; safety still refuses. Exact next:
required gate, signed routine delivery, one pending attempt to identify the actual
consumer; correct only the demonstrated coordination cause. No owner action,
policy/permission/resource change.132 earlier windows/current outcome unconfirmed,
main graph unavailable and source guard retained. Readiness/reconciliation pending.

## Current — reuse the proven full-database restore capacity

PR96/607280c delivered automatically (sequence15). Its bounded recorder now retains
living frames after process death; latest emitted frame was a zombie. The decisive
existing Fjolsvidr evidence predates this incident: logbook-live-edge-index-2026-11-05
records11.75GiB resident for this shared graph; logbook-graph-restore-2026-11-05 and
logbook-repair-rehearsal-2026-11-05 record successful same-image full restores under
18GiB/2CPU. The prior1/4GiB caps were smaller than known actual database memory.

Branch fix/reuse-proven-recovery-capacity reuses18GiB/2CPU for this exact named
volume, requires36GiB available host memory, keeps fixtures1GiB/1CPU, permits at
most120 readiness attempts within180sec worker/240sec remote deadlines, and
classifies zombie samples as dead. Image/volume, data recovery, durability,
receipt/startup gates, signed policy4 and500/5000 record limits stay unchanged.
31 focused tests pass after capacity/readiness/zombie regressions first failed;
Ruff/types pass. Exact next: required gate, signed routine publication/delivery,
one pending recovery; verify retained outcome, receipt-guarded restart and normal
independent source-holder release, then finish reconciliation. No owner action.
132 earlier retained; current outcome unknown/graph inactive/guard held until proof.

## Current — preserve useful startup evidence after process exit

PR95/966f0b2 merged542d707; publisher43430475264 passed and native machine-only
delivery returned RELEASE_READY542d707 (signed sequence14). No owner interaction.
The one-thread INFO attempt failed before connection; repeated dead-process samples
erased all living frames. Thread-count change did not resolve recovery. Current
branch fix/preserve-final-startup-sample keeps the first dead frame and final
living frames within the same six-frame bound. Regression reproduces the data
loss before the fix; no resource/policy/permission changes. Exact next: required
gate, signed routine delivery, one pending cycle, inspect actual retained INFO
startup evidence and repair the demonstrated cause. Do not raise caps blindly.
132 earlier retained; bafc7fb outcome unknown, graph inactive, source guard held.

## Current — automatic recovery reached real startup, 2026-11-13

Owner source-updater repair receipt and independent native source pointer verify
sequence13/32b88b1 installed on Mimir. Automatic fixed delivery returns RELEASE_READY
32b88b1 with empty stderr: Odin delivery succeeded without an owner key. One
pending cycle reached named-offline but failed; live last frame shows memory.current
4294406144 vs4294967296 maximum, RSS4145108kB,41threads/32visibleCPUs and237/241
CPU-throttled periods. No listener and WARNING log empty. Outcome remains unknown.

Branch fix/delegated-recovery-startup matches the existing CPU1 allowance with
storage-recovery-thread-count=1 and INFO private startup logs. No memory cap,
policy, permission, image, volume, durability, receipt or startup-gate change.
Regression first failed on absent thread setting, then passes. Exact next:
required gate, one signed routine publication/delivery, one pending cycle and
inspect actual startup logs. This is a measured concurrency hypothesis, not proof
of the complete memory cause. No owner intervention needed.132 prior retained,
graph inactive/source guard retained; reconciliation/readiness pending.

## Current — repair source updater bootstrap, 2026-11-13

Standing policy4 is active on both hosts (signed abb99da, sequence11). The first
new-version machine-only delivery of PR93/b0f407e failed before installation:
the old source installer passed public0755/0644 generation metadata into a
private-only verifier. Source/Odin remain11; no new repair was started.

Branch fix/source-update-bootstrap verifies public metadata through a private
copy and runs the freshly signed installer in a separate interpreter. Existing
policy, trust, permissions and500/5000 limits are unchanged. Real consecutive
signed source updates now exercise the actual modes; an empty root namespace
rehearsal updates11→13→14 and repeats each version without an owner private key.
The original fixture missed this second real update; installation/idempotence
alone was insufficient. See docs/source-updater-bootstrap.md.

Exact next: publish this correction once, stage the verified narrow fixed-loader
repair, then one Mimir administrator authentication is technically necessary:
installed updater11 cannot replace itself and sudo -n confirms no root access.
This is a routine bootstrap bug repair, NOT a severe policy change; no owner
signature or private Odin login is requested. After repair, use existing fixed
machine-only delivery and one pending recovery cycle; inspect live memory before
further resource changes.132 earlier retained windows; bafc7fb outcome unknown,
graph inactive, source guard preserved. Readiness remains pending. Earlier next
actions below are historical and superseded.

## Current — machine-only cold recovery correction, 2026-11-13

Owner receipts confirm standing maintenance active on BOTH hosts (abb99da,
sequence11); ordinary machine-only release delivery returns RELEASE_READY.
No owner-key request is needed. New branch fix/automatic-cold-recovery-evidence
connects saved restarted targets to the existing offline/authorize commands,
checks actual recovery/outcome before normal source-holder release, reuses verified
receipts and handles interrupted authorizer markers. A bounded private live startup
sampler preserves logs before process death; caller errors retain both streams.
No policy/permission/limit/cap changes.7 focused tests and existing10/35/11 checks,
Ruff/types pass; full commit gate mandatory. Real isolated3.13.2 process sampler
passed four observations and was removed. See docs/automatic-cold-recovery.md.
Exact next: publish once, use fixed machine-only delivery, run one pending cycle,
inspect live evidence, fix actual OOM cause and finish reconciliation.132 prior
retained; pending outcome unknown/graph inactive/guard retained. Earlier owner
activation requests below are historical, already satisfied.

## Current activation handoff — 2026-11-13

[PR92](https://github.com/bprager/logbook/pull/92) merged asabb99da65a9e60e882c7df9d2ad1cf41e1721169 (implementation3c2ec90920d3bb120101881b66c2143a25d54f56). Signed publisher43379432367 passed; sequence11 digestbcc2afe90b34b0174ec7bcc78a4fa85fbd740d9669ddbf40575a2c3efaeb9e67 is staged. Required gate672 tests/21 skipped,99% changed-line coverage passed. Actual published package passed disposable root Odin migration/idempotence/rollback and Mimir migration/idempotence/visudo rehearsals;12 Fjolsvidr transition/legacy tests passed. Four staged per-version signing commands are retired.

Production standing maintenance delegation remains PENDING. Existing404d1cb is
installed; its4GiB isolated cold recovery also OOM-killed.132 prior retained
windows; pending bafc7fb outcome unknown, graph inactive, source guard retained.
No production code/policy/permission/startup changed by this handoff.

Exact next ON MIMIR: `/usr/bin/python3 ~/.local/share/FjolsvidrAccessMigration/logbook-standing-maintenance-abb99da/delegate-maintenance.py --apply`.
This is one new severe trust/permission delegation: ONE standing-policy signature,
private recovery SSH login and local administrator authentication. No executor
signature or repair starts. Retry reuses existing policy signature. Then verify
actual activation and use restricted automatic updates/diagnostics/recovery;
never ask for a new signature just because code/version changed. Diagnose live
memory evidence before resource changes; do not force-start graph/clear guards.
Original reconciliation/readiness remains unfinished. Earlier next actions below
are historical and superseded. Evidence lives in Fjolsvidr
maintenance-authority-correction-2026-11-13/assessment.md.

## Current — routine maintenance delegation, 2026-11-13

Implementation `3c2ec90920d3bb120101881b66c2143a25d54f56`; [PR92](https://github.com/bprager/logbook/pull/92). Required commit gate also passed after documentation corrections.

Owner correction: routine maintenance/development must never request per-version
owner signatures. Implemented explicit version4 standing delegation to the
existing signed publisher, automatic Odin executor/helpers and Mimir immutable
source/launchers, update locks, ordering and rollback. It explicitly trusts the
publisher as a privileged-code supplier; policy/trust/permissions changes still
need owner intervention. Existing500/5000 bounds remain.

Validation: required gate passed672 tests/21 skipped, lint/types/docs/watcher build
and99% changed-line coverage. Crypto test deletes the fixture owner private key
before routine upgrades. See docs/standing-maintenance.md and gate evidence.
Production is NOT delegated yet. Owner output proves404d1cb installed, but its4GiB
cold recovery also OOM-killed;132 prior retained windows/current outcome unknown.
Exact next: publish this implementation once, stage/rehearse the single policy-only
signature transition on both hosts, then activate that severe delegation and use
ordinary automatic updates/recovery to close the existing incident. Do not offer
old per-version signing commands. Earlier handoffs below are historical.

2026-11-13: Owner Docker events prove the isolated named recovery container hit OOM and died137 about9sec after launch. Host MemAvailable56069312kB; 1GiB scratch cap is insufficient. Branch fix/offline-recovery-memory raises only named recovery to4GiB with at least8GiB available host memory required; CPU1, network-none, exact image/volume, 500-write policy, receipts and restart protections remain. Fixture cap stays1GiB. Cleanup confirms already-removed container absence without masking the worker failure. Not installed or qualified yet; protected executor remains74a069ec,132 earlier retained windows, current outcome unknown. Exact next: finish required gate, publish signed correction, stage tested exact owner executor update plus the existing cold recovery in one owner login, then confirm actual outcome and source-guard release and resume reconciliation. No repeated diagnostic retry under the insufficient cap. Fjolsvidr PR377/Logbook implementation PR to follow; no readiness claim.

2026-11-13: Owner child stderr proves isolated retain-worker connectivity was refused at 127.0.0.1:7697 throughout its 30-attempt check. Restart verified; journal/completion present; no verified recovery/restart. Underlying database startup cause still unknown. Branch fix/offline-startup-diagnostics adds bounded read-only temporary-container state, logs (both streams) and events before existing cleanup; one attempt, original exception retained, no executor/policy/permission/resource changes. Seven focused checks, Ruff/shell/diff and native byte/mode match pass. Exact next ON MIMIR: `/bin/sh ~/.local/share/FjolsvidrAccessMigration/logbook-recovery-bafc7fb/recover-restarted-window.sh` then complete output; one private SSH login, no new signature. Inspect startup evidence before changing behavior. 132 earlier retained windows; current outcome unknown; no repair supervisor; source guard not force-cleared. Evidence: offline-startup-diagnostic-2026-11-13/assessment.md; PR377. Full reconciliation and readiness pending. Earlier handoffs are historical.

2026-11-13: Owner output now proves Odin restarted; exact bafc7fb journal and completion exist, stopped.json/offline receipt absent. Installed isolated retain worker exited 1, but captured child stdout/stderr were hidden; underlying cause remains unknown. No recovery/restart confirmed; 132 earlier retained windows, pending outcome unknown, no repair supervisor. New branch fix/offline-recovery-error-evidence adds exact child-command/stdout/stderr reporting and rethrows unchanged, no retry or protected executor/policy/permission change. Six focused tests (new regression red then green), Ruff/diff and native byte/mode checks pass. Exact next ON MIMIR: `/bin/sh ~/.local/share/FjolsvidrAccessMigration/logbook-recovery-bafc7fb/recover-restarted-window.sh` and provide complete output; same bounded installed recovery with diagnostics, one private SSH login, no signature. Inspect actual child cause before fixing. Do not directly restart Memgraph or clear guards. Evidence: offline-child-diagnostic-2026-11-13/assessment.md; PR377. Original reconciliation and readiness remain pending; prior next instructions below are historical.

## Current recovery handoff — 2026-11-13

Owner inspection reports the exact bafc7fb669f24824bdbe6d89607cfb61 worker,
recovery service and timer absent, Memgraph inactive, and stopped.json absent.
The old same-boot adapter refused before recovery. A host restart is not yet
proven. 132 earlier repair windows are retained; this window's outcome is unknown.
Both repair supervisors are terminal; the source guard remains with its independent
holder. No new repair, direct startup or forced guard release was performed.

Fjolsvidr implementation `af7e2f6a` on `fix/reboot-recovery-investigation`,
[draft PR #377](https://github.com/bprager/Fjolsvidr/pull/377), reuses the installed
signed offline recovery, receipt verification and guarded restart paths. It first
requires the exact protected boot-target record and a proven boot-ID change.
It refuses changed targets and never fabricates stopped.json or journal evidence.
Five focused adapter tests, eight existing offline/restart tests, temporary-file
integration with simulated service boundaries, native byte/mode checks, lint,
shell checks and focused review passed. These are not live Odin recovery proof.
Protected executor remains `74a069ec`; policy and permissions are unchanged.

**Exact next on Mimir:**
`/bin/sh ~/.local/share/FjolsvidrAccessMigration/logbook-recovery-bafc7fb/recover-restarted-window.sh`

This requires one private recovery SSH login, no new approval signature or
strategic approval. After verified recovery/restart, use the existing routine
pending check to confirm the outcome and normal source-guard release, then resume
bounded repair and finish complete record reconciliation, approved-field replay,
extra-record preservation/classification and health checks. Automatic cold-start
selection is not implemented; full readiness and the original goal remain pending.
The rejected retry is separately recorded as operator-task.1de9a84e99c63d54bce7a53a7f97bad4b0e37810f2dd3b23f3ac2bb7e9da5269;
replay/fresh read and automatic review passed, overall benefit remains missing.
Earlier next-action entries below are historical and superseded by this handoff.

Evidence: Fjolsvidr `docs/evidence/developmental-partnership/restarted-window-recovery-2026-11-13/assessment.md`; [Logbook draft PR #90](https://github.com/bprager/logbook/pull/90).

## Current protected recovery hold — 2026-11-13 03:48 UTC

132 actual repair windows retained. Both supervisors73425/4635 are terminal.
4635 cycle7 timed out444.392sec waiting390sec for independent holder51095.
Fixed pending checks through03:48 return recovery-pending, exit0/empty stderr;
source guard exactbafc7fb669f24824bdbe6d89607cfb61 remains; holder live16:27.
Conservative installed retry allowance passed; actual protected remote state and
underlying cause are unknown. No new repair, forced release or readiness claim.

Exact next ON MIMIR:
`/bin/sh ~/.local/share/FjolsvidrAccessMigration/logbook-recovery-bafc7fb/inspect-and-retry.sh`.
One private recovery SSH login collects exact service/journal output, then uses
the existing guarded retry only if its checks permit. No new approval signature,
executor, standing policy or permission change. Four existing safety tests,
old/wrong-type target refusal, combined compilation, read-only inspection checks,
non-root/arguments refusal, native byte/mode match, shell/Ruff/diff checks and
focused read-only review pass. Canonical source unchanged; staged artifact in
Fjolsvidr transaction-drain-recovery-2026-11-13/bafc7fb-recovery, draftPR377.
Do not run old updater or start another loop while pending. After actual output,
confirm normal pending outcome and source-guard release, finish bounded repair,
full source/graph comparison, approved-field replay, preserved extras and final
recovery/health/original-goal audit. Timeout ledger1915dc34 separately failed,
pending outcome unknown; replay/fresh read and event review passed, benefit
missing/no authority. Earlier live-loop/retry instructions below are historical.

## Current continuation — 2026-11-13 03:23 UTC

Supervisor73425 stopped on remote named-launch error after18 retained windows.
Routine pending check2676 returned not-retained, exit0/empty stderr; source guard
absent. No underlying remote cause is exposed; preserve the error as unknown.
Single replacement4635 retained its first window and next scan passed (130 total).
At the quiet point, complete properties on the three metadata-job projections
showed exactly5 differences, no structural conflict, but6 nodes/6 links still
missing; normal replay remains deferred until bounded repair fills them.

Exact next: supervise4635 to terminal, refresh all-record comparison, lawful
normal replay of accepted source decisions after its precondition, preserve/
classify extras, verify recovery/guard/health and record final truthful outcome.
No owner action, new signature, policy/executor/permission change or force release.
Fjolsvidr evidence/current checkpoint e56ed2b9, draftPR377. Earlier73425/retry
instructions below are historical. Failed-outcome ledger8cf63a77 and separate
recovery recordd9a5772f passed insert/replay/fresh read; neither claims overall
benefit or grants authority.

## Recovery confirmed; bounded repair resumed — 2026-11-13 UTC

The owner retry recovered exact6fcb354 and confirmed retained outcome/startup
permission. Routine pending call45430 returned retained, exit0/empty stderr;
source guard absent, old holder exited and both locks free before resuming.
111 retained windows including the recovered incident; sole supervisor73425 has
added three more as of02:18 UTC (114 total). Policy, executor and permissions
unchanged; no owner action currently needed.

Actual quiet-point comparison found271/271 jobs but1698 missing nodes/11374
missing links,28 extra nodes/2135 extra links and5 old fields on3 existing jobs.
Source projection refresh matched; graph counts stable while collected. Other
content properties were not compared. Preserve extras; totals alone do not close
the gap. Current protected repair is create-only and will not fix those fields.
Read-only authority review supports the existing normal job-scoped projection
path for prior approved source decisions, conditional on comparing the complete
three projections first (293 nodes/517 links in private preview, no writes).

Exact next: supervise73425 to terminal, refresh comparison, then use normal sync
only if its effective changes match approved source updates; otherwise assess
additional changes. Verify recovery/guard/health and record final truthful ledger,
evidence and original-goal audit. No policy expansion, forced release or source
reclassification. Fjolsvidr evidence: transaction-drain-recovery-2026-11-13,
commit7b5a4a9c, draftPR377. This supersedes old retry instructions below.

## Exact guarded retry staged — 2026-11-13 UTC

Signed sequence8 executor74a069ec is installed. Standing policy and permissions
remain unchanged.110 actual repair windows returned retained. Last valid service
snapshot at00:11 UTC matched271 source/271 graph jobs; graph health still drift,
one new arrival awaiting ordinary owner review, zero cleanup-eligible unreviewed.
Full current node/link/property reconciliation is not yet proven.

Current supervisor48339 is terminal: cycle89 timed out after441.988sec total,
waiting390sec for independent source holder16943. Existing fixed pending checks
returned recovery-pending through01:04 UTC; source guard and holder remain.
Owner protected state/journals now confirm all three attempts failed at
`drain_transactions`, first final drain then initial drain, followed by start-limit
refusal. Recovery is failed/failed/exit-code with no process; worker/timer inactive,
Memgraph service running. Underlying active-transaction/listing cause is not yet
observed. No new repair loop, forced marker release,
service restart, policy change or owner signing was performed.

Exact next ON MIMIR: `/bin/sh ~/.local/share/FjolsvidrAccessMigration/logbook-recovery-6fcb354/retry-pending-recovery.sh`.
The existing reviewed retry adapter is staged with only its exact target changed
to6fcb354f44694457bf18d0b4e93f0506. Four existing safety tests against that candidate,
wrong/old target refusal, AST-only-pin equivalence, native byte matching, Ruff and
shell checks passed. It verifies installed signed executor, private exact stop
proof, target/fence and failed/inactive retry state, emits redacted transaction
listing diagnostic and invokes the installed guarded retry. It does not clear
fences directly or change code/policy/permissions. Existing outcome/binding checks
precede startup permission; source guard release stays with its independent holder.
Owner privately unlocks the protected SSH recovery login; no new approval
signature is requested. Paste output. Then recover only this pending window,
finish actual identifiers/classifications/extras/recovery/guard/health comparison
and the original goal audit. New repair has not started. Do not run the old updater.
Do not ask for a generic proceed or repeat strategic approval.

Fjolsvidr draft [PR377](https://github.com/bprager/Fjolsvidr/pull/377) contains actual
receipts and `bounded-retry-runtime-2026-11-12/` evidence. The distinct coordinator
failure is recorded in the existing private outcome ledger as
`operator-task.188f049b542185bfccf509e36ef72b6a7d423ac328d08a0f3caa02be23462e44`;
replay/fresh read passed, operator-reported, benefit missing, no runtime authority.
Existing source implementation gate:664 tests passed/21 skipped, lint/types/docs/
watcher build/changed-line coverage passed. This update changes status only. Current retry artifact and tests are in Fjolsvidr
`transaction-drain-recovery-2026-11-13/assessment.md`, PR377.
This supersedes earlier next actions below.

## Bounded recovery watchdog retries — 2026-11-12 UTC

Actual protected repair retained additional windows but again left recovery
unconfirmed. The existing watchdog runs only once; an owner-triggered guarded
retry of the earlier incident succeeded with zero active transactions. Reuse
systemd failure restart for that same recovery command, bounded to three total
starts over the unit lifetime, five seconds apart. Explicit oneshot type makes
the existing 240-second start deadline cover recovery execution. Manual guarded
retry reuses those properties with nonblocking service submission. No repair is
restarted, and target/stop-proof/drain/fence checks, standing policy and role
permissions are unchanged. Focused 22 launcher tests passed, including the new
bounded-watchdog regression; full required commit gate passed (lint, types,
documentation, watcher build, suite and changed-line coverage). systemd 255
unit-parser verification passed in the ordinary VM; a real restart-success/limit
smoke is a blocking owner-update preflight, not yet runtime proof. Implementation
`4abfec22920cac518948cf198e679fef92900fe7`, [PR #89](https://github.com/bprager/logbook/pull/89).
Signed publication and protected-update staging are next. Production remains on the old executor with a
pending guarded recovery; do not clear it or claim readiness. After validation,
publish the signed one-file launcher correction and prepare exact owner recovery
plus executor replacement, then continue routine repair without per-window
intervention. See `docs/recovery-watchdog-retries.md`.

## Complete-plan schema preflight bound — 2026-11-12 UTC

Actual protected repair retained two jobs, then stopped before writing job 125:
its 574-node/1,221-relationship source plan is within signed v3 policy but the
read-only schema check retained an obsolete 500-node bound. Reuse the shared
MAX_PLAN_RECORDS (5,000) for this check; write batches remain 500 and policy is
unchanged. The 574-node regression failed before the fix and passes afterward;
above-limit refusal remains covered. Full quality gate passed 663 tests (21
skipped), Ruff, mypy, Markdown and watcher build; changed-line coverage is 100%.
Read-only review found no blocker. Actual unstarted recovery returned not-retained
and the source guard cleared. No protected executor was patched or approval
forged. Exact next: publish the canonical signed candidate and prepare its narrow
owner-approved protected executor update; then continue actual bounded repair.
Implementation: `5a54e0b3bc0b6218ae9ef6c0c38753bb10424de9`;
[PR #88](https://github.com/bprager/logbook/pull/88).
Gate log: /tmp/logbook-whole-plan-preflight-gate.log.

## Recovery-only source guardian gateway — 2026-11-11 UTC

Implemented a separate `--recovery` profile in the existing standalone gateway.
The existing source-holder key can be bound to only status, outcome and safe
cancellation; it cannot receive/install releases, discover, stage, authorize or
launch repairs. Unknown profile arguments fail before execution. Default operator
behavior remains compatible. This closes the code prerequisite for replacing the
source key's inactive diagnostic mapping without sharing the protected operator
identity. No live key, sudo rule, owner policy, executor or graph was changed.

Validation: 21 focused gateway/guardian tests passed. The full local gate passed
662 tests (21 skipped), Ruff, mypy, Markdown checks and the watcher build;
changed executable-line coverage is 100% (7 lines). Read-only code review found
no blocking issue. Full gate output: `/tmp/logbook-recovery-profile-gate.log`.
Implementation: `b43bc146b475d8588131ce843cad7297d7272ebe`;
[PR #87](https://github.com/bprager/logbook/pull/87). Exact next:
publish a fresh signed source release, include this profile and the exact source
guardian permission in the pinned final installation, and verify actual role
separation before fresh final severe-change approval. The previous `6788f52`
package remains historical staged evidence and must not be treated as containing
this new profile. See `docs/recovery-only-gateway.md`.

## Restricted backup deployment settings — 2026-11-11 UTC

The tracked SOPS settings still selected the old administrator SSH identity and
unrestricted backup root, despite the already verified restricted live `.env`.
Aligned all four backup settings with that live configuration: dedicated key,
restricted root, restricted transfer mode and compatible rsync executable.
Also corrected the tracked 24-hour retention value to match the live and required
168-hour gate. SOPS integrity verification passed; every other decrypted setting
is unchanged. Updated the sample and backup guide to use the same restricted
configuration. The full commit gate passed 659 tests (21 skipped), lint, types,
Markdown and UI build; changed-line coverage has no changed executable lines.
The existing configuration loader selects the same four values. No decrypted
secret was printed or written to a temporary file. Runtime `.env` and running
services are unchanged. The old administrator key has not been revoked.

Next integrate this configuration correction and update the matching tracked
settings in the live checkout without touching its existing local edits, then
continue administrator/source separation and pending final graph-policy approval.
The signed repair source code remains unchanged; production repair is inactive.

## November 11 oversized graph source plans

Package follow-up: the shared validator is now owned by the standalone installer,
with a regression check using an isolated interpreter and disabled site packages.
This corrects a project-import dependency in the first published candidate before
any activation. Verify publication of this correction before executor staging.

The candidate preserves each complete producer-signed source plan and selects
at most 500 missing records for each independently recoverable window. Policy
versions 1 and 2 retain their existing full-plan limits; version 3 explicitly
separates a source-plan limit (at most 5,000) from the fixed 500-record write
limit. All 71 currently absent job plans passed structural validation; the
largest contained 3,923 records. Two isolated Memgraph tests passed, including
three-window recovery and reversal with unrelated data preserved.

This is not deployed or activated. Next integrate and verify the signed release,
then qualify independent source/administrator authority and the exact protected
executor transition before requesting the fresh owner policy approval. Existing
discard semantics are preserved: discarded jobs retain their status, notes and
audio; no source job was reactivated. See
`docs/plans/2026-11-11-oversized-graph-source-plans.md`.

## November 5 graph repair safety

An experimental bounded repair/rollback module has isolated graph tests. It is
not enabled for live use. It now requires node uniqueness constraints and locks
relationship endpoints during each transaction and validates canonical IDs and
types. Grouped operations reduce repeated graph searches, and relationship-only
batches can be reversed. Isolated tests cover the observed races, and all current
planned nodes and relationships match the canonical format. Other writers are not forced to honor
this format; constraint changes and live rollout remain unverified. Resolve these
gaps and validate recovery before any repair. Preserve stale data.

## November 5 audio cleanup protection

Preserve unreviewed recordings until reviewed and block cleanup when the final
note is missing. Scheduled cleanup remains disabled. Isolated release
`4e3041d53a9cd348766dfa5e9617bcab865ad1b9` serves a live plan blocking all
three unreviewed recordings; protected copies match their checksums. Next
review the three recordings and restore missing notes deliberately. Separately
investigate graph drift using an exact dry run before any graph writes.

## October 5 diary-routing repair

Restored jobs 213/214 (September 30 and October 2), verified canonical content and
daily-sync visibility, preserved audio and unrelated notes. Added deterministic
ASR marker aliases and audited `--skip-entity-linking` support. See
`lessons-learned.md` and `docs/dead-letters.md`.

Updated: 2026-05-17

Project key in memgraph: `logbook`

## Dependency Map

```text
LGB-001 Foundations
  -> LGB-002 Configuration
    -> LGB-003 SQLite ledger
    -> LGB-004 Path builders
    -> LGB-005 Mount probe
    -> LGB-007 Odin job contract

LGB-005 + LGB-003 -> LGB-006 Copy and dedupe ingest
LGB-007 -> LGB-008 ASR worker -> LGB-009 Transcript persistence -> LGB-010 Prefix classifier
LGB-004 -> LGB-011 Markdown renderers
LGB-002 -> LGB-025 Obsidian CLI vault workflow
LGB-010 + LGB-011 + LGB-025 -> LGB-012 Log inbox writer
LGB-010 + LGB-011 + LGB-025 -> LGB-013 Category note writer
LGB-010 + LGB-011 + LGB-025 -> LGB-014 Dead-letter writer
LGB-012 + LGB-003 + LGB-004 + LGB-025 -> LGB-015 Consolidation engine -> LGB-016 Late-arrival rebuild
LGB-010 + LGB-007 -> LGB-017 Meeting diarization
LGB-017 + LGB-011 + LGB-025 -> LGB-018 Meeting note renderer
LGB-003 + LGB-012 + LGB-014 + LGB-015 -> LGB-019 Status API -> LGB-020 Bounded action API
LGB-006 + LGB-019 -> LGB-021 launchd packaging
LGB-003 + LGB-006 + LGB-009 + LGB-012 + LGB-013 + LGB-014 + LGB-018 + LGB-025 -> LGB-026 Audio retention cleanup
LGB-012 + LGB-013 + LGB-014 + LGB-016 + LGB-018 + LGB-020 + LGB-025 + LGB-026 -> LGB-022 End-to-end acceptance tests
LGB-003 + LGB-009 + LGB-017 + LGB-018 + LGB-019 + LGB-024 -> LGB-027 Proof-carrying memory graph
LGB-027 -> LGB-028 Action candidate resolution
LGB-027 -> LGB-029 Memory graph health and drift check
LGB-015 + LGB-021 + LGB-025 -> LGB-035 Daily log entity linker
LGB-014 + LGB-015 + LGB-020 + LGB-035 -> LGB-036 Dead letter management script
LGB-021 + LGB-022 -> LGB-030 Production launchd rollout
LGB-019 + LGB-021 -> LGB-031 Prometheus metrics and scrape integration
LGB-003 + LGB-026 -> LGB-032 Saga backups and restore drill
LGB-027 + LGB-029 -> LGB-033 Memory graph prune and drift repair
LGB-022 + LGB-030 + LGB-031 + LGB-032 + LGB-033 -> LGB-034 0.2.0 release readiness
LGB-034 + LGB-035 + LGB-036 -> LGB-037 1.0.0 stable release promotion
LGB-037 -> LGB-038 1.0.1 mount ingest hardening patch release
LGB-003 + LGB-019 + LGB-021 + LGB-031 + LGB-038 -> LGB-039 Pipeline observer and watch program
LGB-039 -> LGB-040 1.1.0 quality gate release
LGB-039 + LGB-040 -> LGB-041 Modern observer UI surfaces
LGB-041 -> LGB-042 1.2.0 modern observer release
LGB-042 -> LGB-043 1.2.1 retention and watch patch release
```

## Milestone 0: Repo And Product Foundations

### LGB-001 - Foundations

Status: Completed

Dependencies: none

Deliverables:

- Confirm implementation language and package layout.
- Define local development commands.
- Add baseline test and lint tooling.
- Keep generated data and secrets out of git.

Acceptance:

- A new Codex session can find context, decisions, backlog, and PRD quickly.
- Repo has clear instructions for safe local-first development.

Completion note:

- Completed as part of the `0.1.0` planning release and subsequent MVP implementation. The repo now has project context, decisions, backlog, PRD, README, tests, lint tooling, ignored local secrets, and repeatable development commands.

### LGB-002 - Configuration

Status: Completed

Dependencies: LGB-001

Deliverables:

- Typed config for processing root, vault root, category aliases, recorder identity, `odin` endpoint, model names, retention, and OpenClaw API bind address.
- Config for Obsidian CLI binary, vault repository URL, local vault checkout path, and audio retention.
- Example config with no secrets.
- Secret loading through environment or ignored local file.

Acceptance:

- App can validate config and print redacted effective settings.
- Missing vault, processing root, and `odin` endpoint errors are actionable.

Completion note:

- Completed through the typed `.env`/`AppConfig` implementation, tracked `.env.example`, recorder/vault/`odin`/retention/OpenClaw token settings, Memgraph URI support, and live validation against the configured host-local `.env`.

## Milestone 1: Safe Ingestion And Transcription

### LGB-003 - SQLite Ledger

Status: Completed

Dependencies: LGB-002

Deliverables:

- Initial SQLite schema and migration table.
- Recording job discovery table keyed by checksum.
- Idempotent checksum-based discovery recording.
- Schema for recording jobs, transcripts, log entries, daily logs, dead letters, and action audit records.
- Transaction helpers and migrations.
- Unique constraints for checksum and canonical daily log date.

Acceptance:

- `ingest-dry-run --record-discovery` records discovered recorder files without copying or deleting audio.
- Duplicate source audio cannot create duplicate jobs.
- State transitions are validated and recoverable.
- Ledger tracks copied, transcribed, routed, consolidated, canonical daily log path, and late-arrival timestamps for the implemented workflow.

### LGB-004 - Path Builders

Status: Completed

Dependencies: LGB-002

Deliverables:

- Pure functions for processing paths and Obsidian paths.
- Month, weekday, timestamp, and slug formatting.
- Test-vault inbox, category, meeting, and dead-letter paths.
- Canonical daily log path generator.

Acceptance:

- Tests cover PRD examples exactly.
- Invalid path variants are rejected for daily logs.
- Canonical daily log paths render as `06 - Timestamps/YYYY/MM-Month/YYYY-MM-DD-Weekday-Log.md`.

### LGB-005 - Sony Mount Probe

Status: Completed

Dependencies: LGB-002

Deliverables:

- Read-only recorder discovery CLI.
- Mount detector for Sony ICD-PX370.
- Probe that validates volume identity before enqueueing.
- MP3 listing that ignores macOS `._*` sidecar files.
- Sony filename timestamp parser.
- No processing if an unrelated volume mounts.

Acceptance:

- `logbook recorder-discover --env .env` validates the connected recorder without modifying files.
- A mount event can be simulated in tests.
- The probe exits quickly and logs a clear result.

### LGB-006 - Copy And Dedupe Ingest

Status: Completed

Dependencies: LGB-003, LGB-005

Deliverables:

- Checksum-based ingest planning dry run.
- Known-vs-new comparison against the SQLite ledger.
- Idempotent copy into `LOGBOOK_PROCESSING_ROOT/inbox`.
- Post-copy SHA-256 verification before ledger status changes to `copied`.
- Copy new recordings to processing inbox.
- Compute SHA-256.
- Preserve original files on recorder during initial ingest.
- Never delete recorder files before the one-week retention cleanup gate.
- Move jobs through `discovered` and `copied`.

Acceptance:

- Dry run reports new and known recordings without copying or deleting audio.
- Reconnecting the recorder does not duplicate known recordings.
- Repeat copy run skips already-copied recordings.
- Failed copies remain recoverable.
- Initial ingest does not delete source files from the recorder.

### LGB-007 - Odin Job Contract

Status: Completed

Dependencies: LGB-002

Deliverables:

- Typed request/response/result contract models.
- Fake `odin` client for local integration testing.
- HTTP `odin` client boundary using scoped bearer-token auth.
- Define submit, status, result, and health endpoints for `odin`.
- Error model for offline, queued, failed, and succeeded jobs.
- JSON schema for ASR and diarization output.

Acceptance:

- Mac Mini side can queue while `odin` is offline.
- Worker responses include model, timing, and segment metadata.
- Fake client can generate transcript JSON without GPU services.

### LGB-008 - ASR Worker

Status: Completed

Dependencies: LGB-007

Deliverables:

- faster-whisper worker on `odin`.
- Configurable model, device, compute type, VAD, and language.
- Complete consumption of lazy segment results before success.
- Deployable FastAPI worker app with `/health`, `POST /jobs`, and `GET /jobs/{odin_job_id}/result`.
- `serve-odin-worker` command for starting the worker on the target host.
- Client-side `odin-health` probe for the configured worker health endpoint.
- Client-side `transcribe-copied` command that submits copied recordings through the HTTP `odin` client.

Acceptance:

- A test audio file returns text, segments, timestamps, and ASR metadata.
- Health endpoint reports model readiness and GPU availability.
- Live `odin-health --env .env` succeeds against the actual host.
- Live `transcribe-copied --env .env` succeeds for at least one copied test recording without using the fake client.

Current note:

- Live `odin-health --env .env` passes against `http://192.168.1.3:8765`; the worker now runs on `odin` as enabled user systemd service `logbook-odin-worker.service`.
- Live `transcribe-copied` validation passed on 2026-04-29 against an isolated copied test recording in `/Users/bernd/VoiceIngest/odin-live-validation-20260429-190415`; transcript JSON included text, segment timestamps, language, and `large-v3` ASR metadata.

### LGB-009 - Transcript Persistence

Status: Completed

Dependencies: LGB-003, LGB-008

Deliverables:

- Fake transcript persistence under `LOGBOOK_PROCESSING_ROOT/transcripts`.
- Ledger fields for `odin_job_id`, `transcript_path`, `transcribed_at`, and `asr_model`.
- Store transcript JSON outside Obsidian by job ID.
- Link transcript path and model metadata in the ledger.
- Transition jobs to `transcribed`.

Acceptance:

- Restarting the daemon does not lose completed transcripts.
- Fake transcript pass writes transcript JSON and updates copied jobs to `transcribed`.

## Milestone 2: Deterministic Routing

### LGB-010 - Prefix Classifier

Status: Completed

Dependencies: LGB-009

Deliverables:

- Normalize first 20 words.
- Strip built-in filler words.
- Match exact aliases for log, meeting, and categories.
- Add constrained fuzzy aliases for safe log-entry variants.

Acceptance:

- PRD examples route correctly.
- Unknown prefixes become dead letters.
- Fuzzy matching cannot turn arbitrary speech into a category.

### LGB-011 - Markdown Renderers

Status: Completed

Dependencies: LGB-004

Deliverables:

- Frontmatter writer.
- Templates for routed transcript notes in the test-vault slice.
- Atomic write helper.
- No source-audio links in rendered Obsidian content unless a non-path job ID is needed for audit.

Acceptance:

- Rendered Markdown matches PRD structure.
- Atomic writes do not leave partial files on failure.
- Generated notes do not expose retained audio paths.

### LGB-025 - Obsidian CLI Vault Workflow

Status: Completed

Dependencies: LGB-002

Deliverables:

- Resolve and validate Obsidian CLI binary.
- Configure vault repo as `https://github.com/bprager/obs-vault.git`.
- Manage local vault checkout/sync path.
- Add `vault-preflight` for non-writing validation.
- Verify the configured `obsidian-cli` vault name is registered before writer use.
- Add configurable Obsidian CLI command templates for sync, status, commit, and push.
- Add serialized write lock around vault workflow commands.
- Add optional `obsidian-cli create` note writer for registered Obsidian vaults.
- Add `--job-id` routing to constrain first production vault writes.
- Provide pre-write sync/status and post-write commit/push workflow.
- Serialize vault writes to avoid conflicting generated changes.
- Surface CLI failures as recoverable job states.

Acceptance:

- A test vault sync can pull, write a generated file, commit/push or dry-run as configured, and report status.
- No GitHub token or CLI credential is stored outside `.env` or the user's existing credential manager.
- Missing CLI or missing vault path fails preflight before writing.
- `obsidian-cli` writer uses vault names and is gated on Obsidian vault registration.
- Local `obs-vault` checkout exists at `/Users/bernd/Obsidian/obs-vault`.
- First real-vault note write succeeded for ledger job 17 through `obsidian-cli`.
- Batch backfill to the real vault succeeded for all 17 inbox notes.
- Git workflow templates are configured for pull, stage, status, commit, and push.

### LGB-012 - Log Inbox Writer

Status: Completed

Dependencies: LGB-003, LGB-010, LGB-011, LGB-025

Deliverables:

- Write `log_entry_inbox` notes under `10 - Logs/00 - Inbox`.
- Strip spoken log prefix.
- Record routed ledger status and relative Obsidian-style path.
- Write through the local Obsidian vault workflow.

Acceptance:

- Log entries remain pending until consolidation.
- Test-vault pilot wrote 17 log inbox notes from fake transcripts with no audio paths exposed.
- Real-vault pilot wrote and pushed 17 log inbox notes through `obsidian-cli`.

### LGB-013 - Category Note Writer

Status: Completed

Dependencies: LGB-010, LGB-011, LGB-025

Deliverables:

- Write notes into configured category folders.
- Strip category prefix.
- Record source audio and transcript metadata.
- Write through the local Obsidian vault workflow.

Acceptance:

- `idea`, `task`/`todo`, `research`/`question`, and `reminder` route to expected folders.
- Category notes are written under `20 - Notes/00 - Inbox/{category}` with the spoken prefix stripped.
- Ledger records `category_written`, the `category:{name}` classification, and the relative Obsidian path.

### LGB-014 - Dead-Letter Writer

Status: Completed

Dependencies: LGB-003, LGB-010, LGB-011, LGB-025

Deliverables:

- Write unknown transcripts into `99 - Dead Letters`.
- Set `delete_after` to 28 days.
- Add status and possible rescue actions.

Acceptance:

- Unknown prefixes are retained and reviewable.
- No hard delete occurs as part of MVP.
- Dead-letter notes are written under `99 - Dead Letters` with `review_status` and a 28-day `delete_after`.
- Ledger records `dead_letter_written`, the `dead_letter` classification, and the relative Obsidian path.

## Milestone 3: Log Consolidation

### LGB-015 - Consolidation Engine

Status: Completed

Dependencies: LGB-003, LGB-004, LGB-011, LGB-012, LGB-025

Deliverables:

- Track open log date.
- Manual consolidation CLI for routed log entries.
- Consolidate routed log entries by date.
- Render final daily logs in timestamp order.
- Record each job's canonical daily log path and consolidated timestamp in the ledger.
- Sync generated daily logs through the Obsidian vault Git workflow.

Acceptance:

- The canonical path exactly matches `06 - Timestamps/YYYY/MM-Month/YYYY-MM-DD-Weekday-Log.md`.
- Only one final daily log exists for a date.
- Real vault pilot generated and pushed 3 daily logs from 17 routed inbox notes.

### LGB-016 - Late-Arrival Rebuild

Status: Completed

Dependencies: LGB-015

Deliverables:

- Detect late entries for already consolidated dates.
- Mark entry as `late_arrival`.
- Re-render the canonical daily log atomically.
- Update `entry_count` and checksum.

Acceptance:

- Late entries are inserted in timestamp order.
- No duplicate daily log variant is created.
- Consolidation detects a new inbox entry for an already consolidated date, marks it as a late arrival, and atomically rebuilds the existing canonical daily log.

## Milestone 4: Meetings

### LGB-017 - Meeting Diarization

Status: Completed

Dependencies: LGB-007, LGB-010

Deliverables:

- Diarize recordings classified as meetings.
- Keep model configurable between PRD baseline and newer pyannote options.
- Persist diarization output and speaker labels.
- `logbook fake-diarize-meetings` command for local contract validation without GPU services.
- SQLite fields for diarization path, timestamp, and model metadata.

Acceptance:

- Meeting jobs include transcript segments and diarization turns.
- Missing Hugging Face token or model access fails clearly and recoverably.
- Non-meeting transcripts are skipped without mutating job state.
- Diarization results without speaker labels fail without mutating job state.

### LGB-018 - Meeting Note Renderer

Status: Completed

Dependencies: LGB-011, LGB-017, LGB-025

Deliverables:

- Write meeting notes under `30 - Meetings/YYYY/MM-Month`.
- Include participant mapping placeholders, summary placeholders, decisions, action items, and transcript.
- Optionally link meetings into daily timestamp notes after consolidation exists.
- Write through the local Obsidian vault workflow.
- Require diarization before rendering meeting notes so speaker labels are available.
- Strip the spoken `meeting` prefix from the first transcript segment in rendered notes.

Acceptance:

- Speaker labels appear as `SPEAKER_00`, `SPEAKER_01`, etc.
- Meeting notes do not expose source audio paths or filenames.
- Transcribed meeting jobs without diarization fail safely without mutating job state.

## Milestone 5: OpenClaw Observability

### LGB-019 - Status API

Status: Completed

Dependencies: LGB-003, LGB-012, LGB-014, LGB-015

Deliverables:

- `GET /health`
- `GET /jobs`
- `GET /jobs/{id}`
- `GET /logs/inbox`
- `GET /logs/open-date`
- `GET /logs/consolidated/latest`
- `GET /dead-letters`
- FastAPI app factory with Swagger UI at `/docs`, ReDoc at `/redoc`, and OpenAPI at `/openapi.json`.
- Optional bearer-token read auth through `LOGBOOK_READ_TOKEN`.

Acceptance:

- OpenClaw can observe queue, inbox, latest consolidated log, and dead letters without shell access.
- Status responses avoid local source audio paths, copied audio paths, and transcript paths.
- Swagger UI is configured with Logbook API metadata and bearer auth.

### LGB-020 - Bounded Action API

Status: Completed

Dependencies: LGB-019

Deliverables:

- `POST /jobs/{id}/reprocess`
- `POST /dead-letters/{id}/rescue`
- `POST /logs/{date}/rebuild`
- Audit log for every requested action.
- SQLite `action_audit` table for accepted bounded action intents.
- Separate action-token auth through `LOGBOOK_ACTION_TOKEN`.
- Optional per-target action idempotency key for safe OpenClaw retries.

Acceptance:

- Actions are validated, idempotent, and scoped.
- No endpoint performs arbitrary shell execution or direct hard delete.
- Action endpoints record auditable intent without mutating job state inline.
- Invalid targets are rejected before audit creation.
- Repeated requests with the same idempotency key return the existing audit record.

### LGB-021 - launchd Packaging

Status: Completed

Dependencies: LGB-006, LGB-019

Deliverables:

- `launchd` plist template using `StartOnMount`.
- Runtime user guidance respecting `clawdbot` OpenClaw ownership.
- Log paths and graceful shutdown behavior.
- Cleanup schedule for one-week audio retention.
- `logbook launchd-render` command that writes host-local plists under `LOGBOOK_PROCESSING_ROOT/launchd` by default.
- Scheduled guarded cleanup command for eligible local and recorder-side audio.

Acceptance:

- Mount trigger starts only the lightweight probe.
- OpenClaw services are not started as `bernd`.
- API service plist uses launchd keepalive and a bounded shutdown timeout.
- Retention job runs on schedule and does not delete audio before the LGB-026 gates pass.

### LGB-026 - Audio Retention Cleanup

Status: Completed

Dependencies: LGB-003, LGB-006, LGB-009, LGB-012, LGB-013, LGB-014, LGB-018, LGB-025

Deliverables:

- Track local audio and recorder-side audio cleanup eligibility.
- Compute cleanup eligibility from SQLite ledger timestamps, not recorder mtimes or YYMMDD filenames.
- Delete or trash local copied audio after one week only after processing, Markdown write, and vault sync are confirmed.
- Delete Sony-recorder source files after one week only after checksum, transcript, derived note, and vault sync are confirmed.
- Record cleanup attempts, successes, failures, and retry eligibility in SQLite.
- Provide cleanup status data for the status API.
- `cleanup-plan` command for read-only eligibility inspection.
- `cleanup-audio --execute` for explicit local cleanup.
- `cleanup-audio --execute --include-recorder` for explicit recorder-side cleanup.
- `GET /cleanup/audio` read-only FastAPI endpoint.

Acceptance:

- Audio is not linked from Obsidian notes.
- No audio is deleted before one week.
- Clock-skewed or manually corrected recorder files are not deleted until their ledger `cleanup_eligible_at` is reached.
- No audio is deleted if processing or vault sync is incomplete.
- Reconnecting the recorder after one week cleans eligible source files and leaves ineligible files untouched.
- Cleanup failures are visible and retryable.
- Cleanup planning is read-only by default and does not persist eligibility or delete audio.
- Recorder-side deletion verifies the source path is under the configured recorder folder and checksum matches.

## Milestone 6: Verification And Refinement

### LGB-022 - End-To-End Acceptance Tests

Status: Completed

Dependencies: LGB-012, LGB-013, LGB-014, LGB-016, LGB-018, LGB-020, LGB-025, LGB-026

Deliverables:

- Fixture transcripts for each routing class.
- Simulated multi-day log consolidation.
- Late-arrival rebuild test.
- Dead-letter rescue test.
- Offline `odin` queue test.
- Obsidian CLI vault sync test.
- One-week local and recorder-side audio cleanup test.
- Guarded `mark-vault-synced` dry-run/execute command that proves generated vault paths are in pushed vault `HEAD` before setting `vault_synced_at`.

Acceptance:

- PRD acceptance criteria are covered by automated tests or explicit manual test scripts.
- Existing pushed vault notes can be proven and marked as synced without deleting audio.

Completion note:

- First production batch acceptance completed on 2026-05-01. Guarded local cleanup and recorder-side cleanup both reached 0 pending actions for the 17 consolidated, vault-synced jobs. Follow-up recorder discovery showed 7 newer MP3 files remaining for the next ingest batch, and verification passed with 78 tests OK, `ruff check .` OK, and memory graph health `status=ok`.

### LGB-023 - Optional Open Log Preview

Status: Completed

Dependencies: LGB-015

Deliverables:

- Optional `10 - Logs/00 - Inbox/Open-Log-Preview.md`.
- Clear marker that the note is generated and non-canonical.
- `logbook open-log-preview` command with filesystem and `obsidian-cli` writer support.
- Date override for testing or explicitly previewing a selected day.

Acceptance:

- Current-day visibility improves without violating the one-final-log invariant.
- Preview note renders entries from routed or already consolidated log jobs without creating another canonical daily log.

### LGB-024 - Summaries And Action Extraction

Status: Completed

Dependencies: LGB-018, LGB-020

Deliverables:

- Optional summaries for meetings and notes.
- Optional action item extraction.
- Human-review workflow before writing derived content into canonical notes.
- `logbook extract-insights` command that writes JSON artifacts under `LOGBOOK_PROCESSING_ROOT/insights`.
- Non-canonical review notes under `40 - Reviews/Logbook Insights`.
- Deterministic first-pass summary and action candidate extraction for task/category notes, logs, and diarized meetings.

Acceptance:

- Generated summaries never overwrite raw transcript or staged source content.
- Review artifacts are marked `needs_review` and `canonical=false`.
- Source notes and canonical daily logs are not modified by extraction.

## Milestone 7: Proof-Carrying Memory

### LGB-027 - Proof-Carrying Memory Graph

Status: Completed

Priority: P0

Dependencies: LGB-003, LGB-009, LGB-017, LGB-018, LGB-019, LGB-024

Deliverables:

- `logbook memory-graph-sync --env .env` dry-run planner that reads SQLite, transcripts, diarization artifacts, generated notes, and insight artifacts without writing.
- Explicit `--execute` mode that idempotently upserts proof-carrying memory nodes and relationships into Memgraph.
- Stable graph IDs for `LogbookJob`, `TranscriptSegment`, `GeneratedNote`, `ActionCandidate`, `Decision`, `Topic`, `Person`, `Project`, and `SourceEvidence`.
- Evidence model that links every derived action, decision, topic, person, and project back to job ID, transcript segment offsets, generated note path, artifact checksum, and source timestamp.
- No source audio paths or recorder paths in graph nodes, relationships, API responses, or generated notes.
- Initial read-only query set for open loops, unresolved action candidates, recent decisions, topic trails, and weekly memory diffs.
- Test fixtures proving idempotent sync, dry-run safety, evidence completeness, and path privacy.

Acceptance:

- Dry-run reports planned node and relationship changes without mutating Memgraph.
- Re-running `--execute` against the same ledger/artifacts is idempotent.
- Every generated memory object has at least one `SUPPORTED_BY`/`DERIVED_FROM` evidence path back to a Logbook job and source artifact.
- The graph can answer "what did I promise?", "what remains unresolved?", and "what changed this week?" using only local data.
- SQLite remains the processing source of truth; Memgraph is the query/memory layer.
- OpenClaw can consume graph-backed memory through bounded read APIs without shell access.

Implementation notes:

- Added `logbook memory-graph-sync` dry-run planner and explicit `--execute` Memgraph upsert path with stable `MERGE` IDs.
- Added `logbook memory-graph-query` and FastAPI `/memory/*` read endpoints for open loops, unresolved actions, recent decisions, topic trails, and weekly diffs.
- Added path-privacy and evidence-completeness tests proving source audio paths do not leave the ledger/transcript artifacts.

### LGB-028 - Action Candidate Resolution

Status: Completed

Priority: P0

Dependencies: LGB-027

Deliverables:

- Durable SQLite review table for memory action candidate resolution state.
- Dry-run-first `logbook memory-action-resolve` command with explicit `--execute` write mode.
- Token-protected FastAPI `POST /memory/actions/{action_id}/resolve` endpoint.
- Memory graph overlay that marks resolved `ActionCandidate` nodes and excludes them from open-loop queries.
- Audit records for API and CLI resolution writes.

Acceptance:

- Resolving an action candidate does not mutate transcripts, generated notes, canonical logs, source audio, or recorder audio.
- Resolved action candidates disappear from `/memory/open-loops` and `memory-graph-query --query open-loops`.
- Resolution writes are durable in SQLite and visible on the next graph build.
- Resolution endpoints remain bounded by action-token auth in the API and dry-run-first behavior in the CLI.

### LGB-029 - Memory Graph Health And Drift Check

Status: Completed

Priority: P0

Dependencies: LGB-027

Deliverables:

- Read-only local-vs-live Memgraph comparison for Logbook-owned graph nodes and relationships.
- `logbook memory-graph-health --env .env` CLI command that reports plan counts, live counts, drift by label/type, and Memgraph reachability.
- FastAPI `GET /memory/graph-health` endpoint registered in Swagger/OpenAPI.
- Tests for matching counts, drift detection, missing Memgraph config, and OpenAPI registration.

Acceptance:

- The health check never writes to Memgraph, SQLite, generated notes, source audio, or recorder audio.
- A freshly synced graph reports `status=ok` when live counts match the local plan.
- Missing or unreachable Memgraph is reported explicitly without blocking local plan generation.
- Count drift is visible by node label and relationship type so the next operator action is clear.

## Milestone 8: Production Hardening

### LGB-030 - Production launchd Rollout

Status: Completed on 2026-05-03

Priority: P0

Dependencies: LGB-021, LGB-022

Deliverables:

- Install or document the exact launchd bootstrap commands for the Logbook API, mount probe, and retention audit on `mimir`.
- Verify rendered plist paths, labels, logs, environment file references, and restart behavior.
- Keep OpenClaw runtime ownership separate: do not start OpenClaw gateway or node services as `bernd`.
- Add an operational check command sequence for launchd status, API health, mount probe dry run, and retention audit output.

Acceptance:

- A fresh operator can install, inspect, stop, and rollback the Logbook launchd jobs without reading implementation code.
- The launchd rollout does not start OpenClaw services under `bernd`.
- Status/API and recorder probe behavior are verified after bootstrap.

Notes:

- Installed `local.logbook.api`, `local.logbook.recorder.mount-probe`, `local.logbook.retention-audit`, and `local.logbook.entity-linker` as `bernd` user LaunchAgents on `mimir`.
- Moved Logbook API from `127.0.0.1:8787` to `127.0.0.1:8788` because `8787` is already owned by the `clawdbot` CashClaw/OpenClaw adapter.
- Verified `curl http://127.0.0.1:8788/health`, `/metrics`, bounded mount-triggered ingest wiring, and read-only retention audit wiring. The original rollout used discovery-only mount wiring; `1.0.1` changes the mount job to `process-mounted-recorder`.
- Verified OpenClaw gateway/node processes remained owned by `clawdbot`; only Logbook launchd jobs run as `bernd`.

### LGB-031 - Prometheus Metrics And Scrape Integration

Status: Completed on 2026-05-03

Priority: P0

Dependencies: LGB-019, LGB-021

Deliverables:

- Add `/metrics` endpoints for the Logbook recorder/status API and the `odin` worker.
- Expose queue depth, job status counts, failed jobs, dead letters, latest consolidation age, cleanup pending counts, `odin` health, and graph health status.
- Document Prometheus scrape targets on `odin` without reverse-proxying the transcription path through `fenrir`.
- Add alert candidates for stale consolidation, failed transcription, cleanup failures, API down, `odin` down, and graph drift.

Acceptance:

- Metrics are path-safe and do not expose source audio, transcript paths, bearer tokens, or local vault paths.
- Prometheus can scrape the configured endpoints over the trusted LAN or loopback path.
- Alert candidates map to clear operator actions.

Notes:

- Implemented `/metrics` on the Logbook API and `odin` worker using Prometheus text exposition without adding an external dependency.
- Documented internal scrape targets and alert candidates in `docs/metrics.md`.
- Metrics are count/status/model gauges only and avoid source audio, transcript, token, and vault path exposure.

### LGB-032 - Saga Backups And Restore Drill

Status: Completed on 2026-05-03

Priority: P0

Dependencies: LGB-003, LGB-026

Deliverables:

- Define SQLite backup semantics that work with WAL mode and avoid raw copying of a live database.
- Back up the SQLite ledger, configuration templates, generated operational artifacts, and selected non-audio state to `saga`.
- Decide whether quarantined local audio is backed up, excluded, or expired before backup.
- Add a restore drill that validates a copied ledger can be opened and queried without mutating production state.

Acceptance:

- A backup run produces a timestamped, restorable artifact set on `saga`.
- The restore drill proves ledger integrity and expected job counts from a backup copy.
- Secrets and raw source audio are handled according to the documented policy.

Notes:

- Added dry-run-first `backup-run` and read-only `backup-restore-drill` CLI commands.
- SQLite backup uses `sqlite3.Connection.backup`; no raw live WAL database copy is used.
- Live target is `192.168.1.3:/mnt/saga/Napoleon/logbook-backups`, which writes through `odin` to the `saga` NFS mount using `/Users/bernd/.ssh/id_rsa_odin`.
- First production artifact set: `logbook-backup-20260503T155634Z`, 34 ledger jobs, 4 audit records, 42 copied artifacts.
- Restore drill from the remote `saga` copy reported `status=ok`, `integrity_check=ok`, schema version `1`, and job count `34`.
- Policy: live `.env`, secrets, source audio, inbox audio, and quarantined/trash audio are excluded.

### LGB-033 - Memory Graph Prune And Drift Repair

Status: Completed on 2026-05-03

Priority: P0

Dependencies: LGB-027, LGB-029

Deliverables:

- Add a dry-run-first graph prune command that identifies Logbook-owned nodes and relationships present in Memgraph but absent from the current local plan.
- Scope pruning to the Logbook memory namespace only, preserving backlog/project/host graph data.
- Add explicit `--execute` mode with count reporting and before/after health checks.
- Add tests for stale generated notes, rerouted dead letters, and removed evidence relationships.

Acceptance:

- Drift caused by reroutes or removed generated notes can be repaired without resetting unrelated Memgraph data.
- Dry run shows exactly what would be pruned.
- Health reports `status=ok` after prune plus sync.

Notes:

- Implemented as `logbook memory-graph-repair --env .env`, dry-run by default.
- The command compares exact Logbook-owned live node/relationship IDs against the local proof plan, upserts missing planned IDs with `--execute`, and prunes stale Logbook-owned IDs only when `--prune-stale --execute` is set.
- Pruning is scoped to IDs in the Logbook memory namespace and does not reset unrelated Memgraph data.

### LGB-034 - 0.2.0 Release Readiness

Status: Completed on 2026-05-03; tagged and pushed

Priority: P1

Dependencies: LGB-022, LGB-030, LGB-031, LGB-032, LGB-033

Deliverables:

- Prepare `0.2.0` release notes covering live `odin` transcription, real vault operation, retention cleanup, memory graph, placeholder cleanup, and production hardening.
- Update README status from implementation-started language to operational MVP language.
- Confirm final verification commands, vault state, Memgraph health, and retention posture.
- Tag and push the release only after explicit operator approval.

Acceptance:

- The release has a clean working tree except approved local-only files.
- README and changelog accurately describe the live operational surface.
- The release tag is created only after explicit approval.

Notes:

- Prepared `0.2.0` release notes in `docs/releases/0.2.0.md`.
- Updated README from implementation-started language to operational MVP release language.
- Updated `VERSION`, `pyproject.toml`, and `Changelog.md` for `0.2.0`; `v0.2.0` is tagged and pushed after explicit operator approval.
- Added tracked SOPS/age encrypted `secrets.yaml` by explicit operator request.
- Release evidence: `odin-health` healthy, Memgraph health `status=ok`, cleanup has 0 pending local/recorder actions with jobs 25-34 only blocked by retention window, saga restore drill `status=ok`, and vault generated content is clean except `.obsidian/workspace.json`.

### LGB-035 - Daily Log Entity Linker

Status: Completed

Priority: P1

Dependencies: LGB-015, LGB-021, LGB-025

Deliverables:

- Add a dry-run-first CLI that scans canonical daily logs from the last configurable number of calendar months.
- Discover linkable Obsidian entities from `04 - People`, `03 - Objects`, and `06 - Timestamps/Meetings`.
- Link unlinked mentions to existing people, event, and object notes while preserving frontmatter, code blocks, existing wiki links, and Markdown links.
- Add a scheduled launchd job that runs the entity linker daily against the last 3 months.
- Backfill the current last-3-month daily logs in the real vault.

Acceptance:

- The command reports files scanned, links inserted, and matched targets before writing.
- `--execute` is required for vault mutation.
- Scheduled launchd rendering includes the bounded entity-linking command and does not start OpenClaw services.
- The real-vault backfill is committed without staging Obsidian workspace state.

### LGB-036 - Dead Letter Management Script

Status: Completed

Priority: P1

Dependencies: LGB-014, LGB-015, LGB-020, LGB-035

Deliverables:

- Add a repo-local `scripts/manage-dead-letters` wrapper.
- Add a dry-run-first CLI for listing pending dead letters, assigning a dead letter to the log route, and discarding a dead letter.
- When assigning to log, write the rescued inbox note, record an audit action, clear stale vault-sync state, rebuild the canonical daily log for that date, and rerun entity linking.
- When discarding, record an audit action and remove the job from the pending dead-letter list without deleting source audio or recorder audio.

Acceptance:

- List mode is read-only and shows job ID, timestamp, current generated note path, and transcript preview.
- Assign/discard require `--execute` for mutation.
- Rescued log jobs flow through daily consolidation and entity linking in one command.
- Discarded jobs remain auditable in SQLite.

## Milestone 9: Stable Operational Release

### LGB-037 - 1.0.0 Stable Release Promotion

Status: Completed on 2026-05-05; tagged and pushed

Priority: P0

Dependencies: LGB-034, LGB-035, LGB-036

Deliverables:

- Promote project metadata from `0.2.0` to `1.0.0`.
- Update README, changelog, release notes, durable Codex status, and backlog for stable operational release status.
- Confirm current verification commands, vault state, Memgraph health, `odin` health, recorder cleanup posture, and backup/restore evidence.
- Tag and push `v1.0.0` after explicit operator approval.

Acceptance:

- Core CLI/API/ledger/Obsidian path contracts are treated as stable release surfaces.
- The release has fresh verification evidence from tests, lint, live `odin` health, Memgraph health, cleanup planning, recorder discovery, and restore drill.
- Local `.env`, Obsidian workspace state, and unrelated local secret edits are not included in the release commit.

Notes:

- The user approved promotion to `1.0.0` on 2026-05-05 after review confirmed no major capability gaps remained.
- Fresh release evidence includes 98 tests passing, `ruff check .` passing, `odin-health` healthy, Memgraph health `status=ok` with 805 planned/live nodes and 1631 planned/live relationships, cleanup at 0 pending eligible actions, recorder discovery showing 5 retained MP3s for still-gated jobs 45-49, and `saga` restore drill `status=ok` for `logbook-backup-20260505T015132Z` with 39 expected/actual jobs.

### LGB-038 - 1.0.1 Mount Ingest Hardening Patch Release

Status: Completed on 2026-05-05; tagged and pushed

Priority: P0

Dependencies: LGB-037

Deliverables:

- Recover the May 5 meeting recording `260505_0919_01.mp3` through transcription, diarization, Obsidian meeting-note routing, vault sync, ledger state, and Memgraph evidence.
- Change the StartOnMount LaunchAgent from discovery-only probing to bounded `process-mounted-recorder` ingestion.
- Harden live ingest around recorder permission errors, long meeting transcription timeouts, MP3-to-WAV diarization normalization, generated-vault roots, and Obsidian workspace state.
- Add `lessons-learned.md` and update README, launchd docs, release notes, changelog, durable status, decisions, and backlog.
- Tag and push `v1.0.1`.

Acceptance:

- Re-running `process-mounted-recorder --env .env` after recovery is idempotent and finds no unrouted candidates.
- The mounted-recorder LaunchAgent arguments show `process-mounted-recorder --env .env`.
- Job 52 is `meeting_written`, has a generated meeting note in `30 - Meetings`, is vault-synced, and is represented in Memgraph.
- The release has fresh lint, unit test, compile, diff, live vault, launchd, and Memgraph evidence before tagging.

Notes:

- Job 52 wrote `30 - Meetings/2026/05-May/2026-05-05T09-19-00-job-000052-meeting.md` and was pushed to the Obsidian vault in commit `796346c`.
- Jobs 51 and 52 are vault-synced after the recovery run; job 50 remains an inbox entry awaiting normal consolidation.
- `lessons-learned.md` is now the durable place for production failure modes and prevention guidance.

## Milestone 10: Operator Watchability

### LGB-039 - Pipeline Observer And Watch Program

Status: In Progress

Priority: P0

Dependencies: LGB-003, LGB-019, LGB-021, LGB-031, LGB-038

Deliverables:

- Add read-only observer documentation in `docs/pipeline-observer.md`.
- Add observer telemetry beside the SQLite job ledger: pipeline runs, stage
  events, heartbeats, progress metadata, and rolling stage-duration history.
- Add a read-only `GET /observer/snapshot` API that reports current run,
  active stage, recent completions, recent failures, stale heartbeat status,
  health summaries, and 24-hour statistics in one consistent response.
- Add `logbook watch` with live refresh, `--once`, `--json`, filters, no-color
  mode, and API-backed remote mode.
- Use measured progress for byte-copy and countable stages.
- Use historical ETA estimates for transcription, diarization, and other
  stages that cannot report real progress.
- Label progress as `measured`, `estimated`, or `unknown`, including ETA
  confidence and sample count for estimates.
- Add path-safe Prometheus metrics for active run, stale run, active stage,
  progress percent, ETA, recent failures, and stage-duration quantiles.

Acceptance:

- `logbook watch --once --env .env` reports recent finished jobs, current run
  state, failures, successes, durations, and basic statistics without writing to
  SQLite, Obsidian, Memgraph, recorder audio, or local source audio.
- `logbook watch --env .env` refreshes compactly in a normal 80-column terminal
  and falls back to plain text when stdout is not a TTY.
- A currently running mounted-recorder pipeline shows current stage, elapsed
  time, heartbeat age, progress bar, ETA, and whether the percentage is measured
  or estimated.
- Sparse history produces `unknown` or `collecting baseline` rather than a fake
  precise ETA.
- Stale pipeline heartbeat detection is visible in the API, CLI, and metrics.
- Default observer output avoids source audio paths, transcript paths, bearer
  tokens, and transcript text.
- Tests cover snapshot consistency, stale heartbeat detection, ETA fallback,
  privacy redaction, terminal/no-TTY output, JSON output, and Prometheus metrics.

Notes:

- Phase 1 landed on 2026-05-15 with a ledger-derived `GET /observer/snapshot`
  endpoint and `logbook watch --once` / `--json` output. This first slice is
  read-only and reports recent finished jobs, durable failures, dead letters,
  and p50/p90 duration statistics.
- Phase 2 landed on 2026-05-15 with `pipeline_runs`, `pipeline_stage_events`,
  the SQLite pipeline reporter, background run heartbeats, mounted-recorder
  stage instrumentation, active-run/stage observer fields, stale-run detection,
  and path-redacted active-stage details.
- Phase 3 landed on 2026-05-15 with `pipeline_stage_durations`,
  stage-duration materialization from successful stage events, legacy
  start/succeeded event rebuild support, and observer ETA fallback using p50
  history, p90 risk duration, sample count, and confidence. Sparse history is
  still reported as `collecting baseline`.
- Phase 4 landed on 2026-05-15 with measured `progress` events, chunked copy
  byte progress, mounted-recorder count progress for route/consolidate/vault
  sync, and observer rendering that prefers measured progress while preserving
  elapsed time from the stage start.
- Phase 5 landed on 2026-05-15 with live in-place refresh, one-shot and JSON
  modes, local SQLite or remote `--api` snapshots, bearer-token environment
  lookup, status filters, failure/stale exit policies, automatic day/night
  appearance, explicit theme overrides, no-color terminal fallback, and optional
  full terminal dashboard mode via `--ui full` with live key controls.
- The watch CLI and observer API now run bounded read-only Odin and Memgraph
  reachability probes, replacing configured-service `unknown` labels with
  `ok` or `unavailable`.
- The observer is a read-only consumer. Bounded recovery actions stay in the
  existing action API and dead-letter management commands.
- SQLite remains the processing source of truth. Observer telemetry explains
  active work and supports ETAs, but it must not decide idempotency or recovery.

### LGB-040 - 1.1.0 Quality Gate Release

Status: Done

Priority: P0

Dependencies: LGB-039

Deliverables:

- Promote project package metadata, API metadata, and release documentation to
  `1.1.0`.
- Add a versioned `.githooks/pre-commit` hook that runs the repository quality
  gate before commits.
- Add `scripts/quality-gate` for manual and hook-driven verification.
- Add a Markdown lint configuration and dev dependencies for coverage and
  changed-line coverage checks.

Acceptance:

- Python files pass Ruff.
- Markdown files pass markdownlint-cli2 under the project style configuration.
- The full unittest suite runs under coverage.
- Python changes must meet at least 97% changed-line coverage through
  diff-cover.
- The versioned hook can be installed with `git config core.hooksPath .githooks`.

Notes:

- Implemented on 2026-05-15. The gate is a coverage ratchet over changed Python
  lines, not a claim that the existing legacy global source coverage is already
  above 96%.

### LGB-041 - Modern Observer UI Surfaces

Status: Done

Priority: P1

Dependencies: LGB-039, LGB-040

Deliverables:

- Add `logbook watch-web` for a packaged web observer UI served on loopback.
- Build the web UI from React, Vite, Tailwind CSS, shadcn-style local
  components, and Lucide icons.
- Add `logbook watch --ui curses` for a full terminal-only dashboard using the
  Python standard-library curses runtime.
- Keep both UIs read-only over the observer snapshot contract.
- Add `mypy` to the quality gate and build the web UI as part of the hook.

Acceptance:

- The web UI starts from one Python command and polls `/observer/snapshot`.
- The web UI automatically chooses day or night appearance from local time.
- The curses UI shows health, current work, progress, ETA context, recent
  finished jobs, failures, and statistics in a compact resize-safe frame.
- Existing compact, full, JSON, once, and remote API watcher modes still work.
- The pre-commit hook runs Ruff, mypy, markdownlint, the web production build,
  unittest coverage, and more-than-96% changed-line coverage.

Notes:

- Implemented on 2026-05-16. The mypy gate starts as an incremental ratchet:
  legacy modules are scanned while the new watcher modules are enforced
  strictly; future work can narrow the legacy ignore surface as typing debt is
  paid down.

### LGB-042 - 1.2.0 Modern Observer Release

Status: Done

Priority: P1

Dependencies: LGB-041

Deliverables:

- Promote project package metadata, API metadata, web UI metadata, and release
  documentation to `1.2.0`.
- Add release documentation for the modern web and curses watcher surfaces.
- Keep the curses dashboard quit affordance explicit with `[q] quit` and tested
  `q`/Escape handling.
- Keep README, changelog, durable Codex context, and packaged web assets aligned
  with the released watcher behavior.

Acceptance:

- `scripts/quality-gate` passes with Ruff, mypy, markdownlint, web production
  build, unittest coverage, and more-than-96% changed-line coverage.
- `logbook watch --once --ui curses` displays the `[q] quit` footer in the
  rendered terminal frame.
- `logbook watch-web` serves packaged assets and a useful server-rendered
  snapshot fallback before JavaScript starts.

Notes:

- Prepared on 2026-05-17. This is a minor release because it adds new operator
  UI surfaces while preserving the observer snapshot contract and existing
  local-first pipeline behavior.

### LGB-043 - 1.2.1 Retention And Watch Patch Release

Status: Done

Priority: P1

Dependencies: LGB-026, LGB-039, LGB-042

Deliverables:

- Promote project package metadata, API metadata, web UI metadata, and release
  documentation to `1.2.1`.
- Fix observer/watch reporting so failed pipeline runs remain visible in the
  failures panel and failed count even after affected jobs recover.
- Change source-audio retention defaults and documentation to the approved
  one-week (`168` hour) cleanup gate.
- Change the hourly retention LaunchAgent to run guarded local and recorder-side
  cleanup with `cleanup-audio --execute --include-recorder`.
- Record live cleanup evidence for jobs 45-90 and the remaining retained Sony
  recorder files.

Acceptance:

- Full unittest discovery passes in the project virtualenv.
- Ruff and whitespace checks pass.
- Fresh cleanup dry-run reports `retention_hours=168`, no local pending cleanup,
  and no recorder pending cleanup.
- Launchd reports `local.logbook.retention-audit` running the guarded cleanup
  command without starting OpenClaw services under `bernd`.

## Milestone 11: Remote Sony USB Ingestion (v1.3.0 planned)

Design: [docs/remote-ingestion.md](../docs/remote-ingestion.md). These items are planned, not delivered.

### LGB-044 - Remote ingestion contract and ledger migration

Status: Implemented on feature branch; release acceptance pending. Priority: P0. Dependencies: LGB-003, LGB-006, LGB-019.

- Additive schema and authenticated resumable intake implemented; focused concurrent, crash/receipt, corruption and revocation tests pass.

- Design authenticated private ingest API, atomic upload sessions, durable receipts, checksum-based server deduplication and migration of the existing SQLite ledger.
- Keep server uniqueness authoritative across two Macs and direct mimir ingest, including already-pruned source audio.
- Acceptance: concurrent and repeated uploads produce one canonical job; crash/retry and lost acknowledgment recover safely.

### LGB-045 - Portable macOS Sony ingestion agent

Status: Implemented on feature branch; first Mac signed read/manual SSH pilot passed. Automatic SSH packaging added; physical login/recovery and second-Mac acceptance pending. Priority: P0. Dependencies: LGB-044, LGB-005, LGB-021.

- Persistent snapshots, resumable transfers, bounded retries and signed mount/login/interval app packaging tested with synthetic recordings.
- Native-architecture compiler selection and staged signing protect remote package updates from Intel-shell/Apple-Silicon tool mismatches.
- Remote-only background app packaging removes recurring Dock presence; read-only watch and checksum-guarded offline eject added. Rebuild the traveling Mac package to apply. Physical Dock/eject acceptance remains pending.

- Package a stable macOS app identity with launchd mount detection, Sony validation, local SQLite outbox, atomic spool, resumable transfer and offline retry.
- Support both MacBook Air machines with separate revocable credentials and no manual steps after installation.
- Acceptance: Sony plug-in works on either Mac; offline and reboot recovery succeed; remote agent never writes to or deletes Sony audio.

### LGB-046 - Unified processing and source-specific retention

Status: Implemented on feature branch; production field acceptance pending. Priority: P0. Dependencies: LGB-044, LGB-045, LGB-026.

- Mount-independent processing and shared pipeline lock added. Synthetic late-arrival, pruned-hash and UUID-bound remote source cleanup tests pass.

- Feed remotely committed audio into existing mimir/Odin/Obsidian pipeline, preserving recorder timestamps and late-arrival rebuild.
- Preserve existing guarded one-week cleanup on direct mimir recorder attachment only; validate checksum, finalized state and vault sync before pruning.
- Acceptance: remote delivery does not prune Sony; reconnecting Sony to mimir prunes only eligible originals and leaves unfinished jobs untouched.

### LGB-047 - Remote observability, security and release acceptance

Status: Implementation and automated acceptance complete on feature branch; first traveling Mac SSH pilot passed with 25 verified recordings. Automatic lifecycle and second-Mac field acceptance pending; production release remains unapproved. Priority: P1. Dependencies: LGB-044, LGB-045, LGB-046, LGB-039.

- Added redacted observer/device/upload/downstream status, bounded request/session limits and authentication audit. Synthetic two-Mac lost-receipt/restart, credential revocation, disk pressure, corruption, downstream outage and guarded retention checks pass.
- Independent review identified duplicate-session quota and idle downstream retry gaps; regression tests and durable recovery fixes are included.
- No production deployment, credential edits, recorder deletion or version/tag promotion.

- Add redacted device/upload status, retries, storage pressure and authentication audit; document network/TCC setup and audio durability/backup decision.
- Test concurrent Macs, lost receipts, corrupted chunks, restart, no network, disk full, credential revocation and existing cleanup regression.
- Acceptance: full quality gate and end-to-end field test pass before version metadata promotion and v1.3.0 release/tag.
