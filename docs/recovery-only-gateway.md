# Source recovery-only SSH gateway

The source-account lock holder must independently confirm remote recovery before
releasing its locks. Its existing fixed client requests only `named-status`,
`named-outcome` and `named-cancel`. Its historical private-key filename contains
`repair-operator`, but that source key must never receive the production operator's
full command profile or private key.

`restricted_repair_gateway.py --recovery` accepts exactly those three existing
requests. It rejects release receipt, bundle installation, discovery, staging,
authorization, launching, offline operations, shells and extra arguments. Unknown
profile arguments exit 64 before any command executes. The no-argument operator
profile retains its existing behavior. Both profiles execute fixed argument lists
without a shell; the existing root dispatcher still validates each target and
only permits safe unstarted-window cancellation.

## Controlled installation

This implementation does not activate a key or grant production permissions.
During the final protected installation, bind only the existing source recovery
key to this fixed SSH command:

```text
restrict,command="/usr/local/libexec/logbook-repair-gateway --recovery"
```

Retain the distinct protected production operator key's no-argument gateway.
Preserve root-controlled key files, gateway code and dispatcher. The source lock
holder also needs its exact no-argument source-account guardian permission; its
private key and root-pinned known-host file must remain separate from the
production operator identity. Do not infer installation from a diagnostic key's
account-name response.

Deployment must use a newly verified signed release containing this change;
older staged packages do not contain the recovery profile. A fresh owner-signed
executor approval and explicit standing policy are still required for production
repair. Normal source recovery and bounded repair must not require per-window
owner signatures.

## Validation and boundaries

Tests exercise the real standalone profile entry, invalid-profile refusal before
execution, the three existing source-client requests, forbidden operator actions,
shell/argument injection and preserved default operator commands. The standalone
entry uses a temporary executable to record dispatch rather than invoking root.
These tests prove command separation; they do not prove a live owner policy,
remote recovery, privileged installation or a production graph repair. Existing
full-flow isolated rehearsals remain separate evidence.

Local validation: 21 focused tests passed; the full quality gate passed 662 tests
(21 skipped), lint, type checks, documentation checks and the watcher build.
Changed executable-line coverage was 100% (7 lines). Read-only review found no
blocking issue. Gate output: `/tmp/logbook-recovery-profile-gate.log`.
