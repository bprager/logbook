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

The owner activated version4 on both hosts at abb99da/sequence11. The initial
fixed source updater required one administrator repair because its old verifier
rejected public generation permissions. PR94 corrected successive updates; the
narrow repair changed only the signed fixed updater, with no new signature/policy.

Actual native machine-only delivery then applied successive releases13–18 on both
hosts without further owner intervention. Cold recovery now uses the already proven
18GiB/2CPU full-database capacity with36GiB available-memory preflight, and waits
only for the exact stopped temporary container's asynchronous removal. The guarded
pending call subsequently returned retained; main graph query succeeded and the
normal source holder released both locks. See PR95–99 and Fjolsvidr's
delegated-recovery-2026-11-13 evidence. No force-start or guard bypass was used.

The later full comparison verifies all271 current source jobs,31428 required
nodes and93655 required links, with zero missing or conflicting records and
zero projected-property differences. PR100 corrected read-only discovery of
large existing jobs; signed sequence19 installed automatically. The existing
source-owner writer refreshed only248 existing nodes/304 verified fields.
All28 surplus nodes/2135 links remain unchanged; their producer identity is
unestablished. Count-health still reports this preserved surplus as drift.
Running application health reports API, SQLite, Odin and Memgraph allok.
Source guard is absent, locks free and repair loops terminal. Current-source
reconciliation/recovery qualification is verified; broader independent benefit
and a complete account of graph history are not claimed. See Fjolsvidr's
final delegated-recovery-2026-11-13 evidence. No owner intervention is pending
for this gap; routine operation continues within the existing standing policy.

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
