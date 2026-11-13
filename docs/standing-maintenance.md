# Standing authority for routine maintenance

The owner must not sign routine releases, bug fixes, diagnostics, recovery,
development or architecture improvements within existing authority. A changed
program hash, privileged code file or release number alone is not a severe change.

## Authority boundary

Version 4 of the owner-signed graph policy explicitly delegates implementation
maintenance to the existing signed release publisher, pinned by public-key
fingerprint. This publisher is a trusted supplier of privileged code: the design
does **not** isolate the system from a malicious signed root program. Changing
that trust, permissions, policy, exceptions or effective authority remains a
severe change requiring the owner. The existing bounds remain 500 writes per
window and 5,000 source records per plan. Earlier policy versions keep their
existing behavior; no delegation is inferred from an old approval.

## Routine operation

The existing merged-main publisher signs an ordered release. The source host's
fixed no-argument maintenance entry verifies the signed policy, pinned publisher,
package digest and increasing release sequence, installs an immutable source
generation, and selects it atomically. Producer and guardian launchers select
that generation. A running source holder keeps its previous generation.

The ordinary release-delivery account then sends metadata through the existing
restricted SSH receiver. A `MAINTENANCE_REQUIRED` response requests the signed
package, not an owner signature. Odin serializes replacement with dispatch and
refuses live workers, recoveries and future timers. Successful terminal watchdog
receipts with `Restart=on-failure` are allowed. The executor, dispatcher, gateway
and installer/receiver helpers update together. Runtime validation failure
restores the previous executor and helpers; accepted release high-water remains.
Both hosts preserve signed policy and trust files during ordinary updates.

Source selection is read again under both update locks, and installed release
sequence is checked before replacement. This prevents a paused old updater from
switching the source host backwards after a newer update.

## Initial activation and limits

The existing production version 3 policy does not grant this maintenance trust.
A single explicit version 4 policy signature and initial root installation on
both machines are required. The reviewed Fjolsvidr `delegate-maintenance.py`
performs this authority transition; it does not request an executor-version
signature. Its Odin login and local administrator authentication are used only
for this initial permission change, not normal maintenance.

The implementation does not resolve the pending cold recovery's memory failure.
The owner's latest output proves executor `404d1cb` installed and the 4 GiB
isolated recovery also OOM-killed. There are 132 prior retained windows; the
pending outcome remains unknown. No direct graph startup or source-guard release
is authorized by a maintenance receipt. Actual production activation and an
ownerless subsequent release remain to be verified after the initial transition.

## Evidence

- Source: `runtime_maintenance.py`, `source_maintenance.py`, signed policy and
  receiver changes, immutable source launchers and committed entrypoint packaging.
- Required local quality gate: 672 tests, 21 skipped; lint, types, Markdown,
  watcher build and 99% changed-line coverage passed. See
  `evidence/standing-maintenance-quality.log`.
- Crypto integration signs the policy once, deletes the fixture owner's private
  key, applies successive publisher-signed releases through the actual receiver,
  and verifies rollback and retained high-water. Source generations, failed
  activation, ordering, real file locks and noninteractive transport are covered.
- Fjolsvidr's disposable root filesystem rehearsal verifies actual old signed
  package installation, new policy signatures, Odin migration/idempotence,
  failure rollback and Mimir source/rule installation. Manager state is simulated;
  no production database or owner private key is used.
