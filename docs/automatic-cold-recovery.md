# Automatic recovery of the saved restarted window

The owner activated standing maintenance delegation on Odin and Mimir for signed
release abb99da (sequence11). The ordinary machine-only release call then returned
RELEASE_READY abb99da without accessing the owner key.

The remaining pending cycle only checked normal watchdog recovery and safe
pre-start cancellation. It never selected the existing guarded cold recovery
after a proven host restart. Remote errors also discarded captured output, and
post-failure diagnostics arrived after the temporary process had disappeared.

## Smallest correction

Reuse existing named-status, named-offline and named-authorize commands and their
unchanged restricted machine identity. Status indicates OFFLINE_REQUIRED only
for the matching saved target after a changed boot and verified inactive guarded
service. An interrupted authorizer's valid marker is accepted only with a verified
current offline receipt, so ordinary replay cannot strand the workflow.

The pending cycle obtains the actual offline outcome, invokes the existing
receipt-checked authorizer, and requires recovered status and outcome before the
normal independent source-holder release. No journal, receipt or stop proof is
invented. No new remote action, policy, permission, record limit or resource cap
is introduced. Existing verified offline receipts are reused rather than running
another isolated recovery.

During the isolated named worker, a background sampler retains six bounded
private observations of process state, cgroup memory and up to four log tails.
It records them with0600 permissions and emits the last living observation on
failure without replacing the original exception. Captured remote stdout/stderr
now reach the private caller. Diagnostic logs are not metrics or public evidence;
actual source content must remain private.

## Validation and actual state

Seven focused regression tests cover machine-route cold recovery, guarded source
release, changed-boot/target/marker checks, interrupted authorization replay,
verified receipt reuse, exact failure streams, real bounded file reads and live
sampling. Existing offline10, named35 and cycle11 checks pass. Ruff and mypy pass.
The required full quality gate is a blocking commit hook.

A real disposable Memgraph3.13.2 instance, network-none/CPU1/memory1GiB, produced
four live host-side samples with file logs and the exact memory cap. The scratch
container was stopped and removed. No production database or owner key was used.

Production remains sequence11 with the saved bafc7fb window pending,132 earlier
retained windows and unknown pending outcome. The old4GiB recovery OOM remains
unresolved. Next: publish this signed maintenance correction once, deploy through
the ordinary fixed release command without owner intervention, then run one
pending cycle to capture live startup evidence. Diagnose that evidence before
another resource change. Never force-start the graph or clear the source guard.
