# Named recovery capacity follows actual restore evidence

The shared graph's recorded resident memory was11.75GiB after index creation:
[Fjolsvidr live evidence](https://github.com/bprager/Fjolsvidr/blob/ops/resume-bounded-repair/docs/evidence/developmental-partnership/logbook-live-edge-index-2026-11-05/assessment.md).
The existing same-image isolated full restore and repair rehearsal both passed
with18GiB and2CPU. See the neighboring logbook-graph-restore-2026-11-05 and
logbook-repair-rehearsal-2026-11-05 evidence directories. This is reuse of the
proven data restore configuration; the previous1/4GiB caps were below known
actual database memory, so they were not appropriate for this shared volume.

Named recovery now permits18GiB/2CPU only when the Linux host reports at least
36GiB available. This leaves at least another18GiB outside the container. The
fixture path stays1GiB/1CPU. Network-none, exact recorded image/volume, WAL/fsync,
no snapshot on exit, no concurrent volume user and all receipt/startup checks
remain. No policy, authority or record-limit change is introduced.

A full restore gets at most120 connectivity attempts instead of30. Its existing
remote action remains bounded240sec and its subprocess is bounded180sec. The
worker validates startup attempts1..120; ordinary fixture default stays30.
The startup recorder treats zombie status as dead, so unusable final process
metadata cannot replace the last useful log/memory frame. Six-frame/private
sampling remains bounded.

Actual native sequence13→14→15 delivery succeeded automatically without owner
intervention. Recovery is not qualified by tests. Next publish once, deliver
through the existing fixed machine route, run the one pending window and require
its actual retained/rollback receipt, guarded restart and independent source
release before starting another repair or claiming readiness.
