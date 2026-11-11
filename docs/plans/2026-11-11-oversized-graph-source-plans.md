# Oversized graph source plans

## Problem and design

Read-only comparison found 71 ledger jobs absent from a separately sampled
196-job graph. Existing source generation built all 71 plans, but 28 exceeded
the repair candidate's 500-record full-plan limit. A plan includes both nodes
and relationships; the largest observed plan contained 3,923 records.

Retain the complete producer-signed source plan and its existing digest binding.
Refresh graph state inside each protected window and select at most 500 missing
records, taking nodes before relationships. Every selected relationship can
therefore refer to nodes present before the window or created in its own batch.
The existing transactional writer, durable journal, batch manifest, and recovery
verification remain responsible for that window. Later windows discover the
remaining records. Retaining a batch does not assert complete job recovery.
Whole-plan validation rejects duplicate identifiers before any partition so
conflicting duplicates cannot disappear between successive windows.

Policy versions 1 and 2 keep their existing full-plan limits. A new version 3
requires an explicit owner signature over these separate limits:

```json
{
  "schema": 3,
  "max_batch_records": 500,
  "max_plan_records": 5000,
  "expires_at": null
}
```

The write limit is fixed at 500; source-plan limits above 5,000 are rejected.
Bundle installation and execution still require the separately owner-approved
protected executor, verified release metadata, and an exact plan digest. Read-only
partial discovery may inspect the larger signed source without granting writes.
No existing policy is rewritten or reinterpreted as version 3.

## Verification

The new tests first reproduced rejection of a 601-node source plan, rejection of
large-plan partial discovery, and an attempted unbounded 1,201-record write.
After the change, focused tests passed. Tests also exercise actual owner/release
signatures and require the protected executor for the new policy version.

The complete local quality gate passed lint, type checks, Markdown checks, the
watcher UI build, tests, and 100% changed-line coverage:

```text
Ran 658 tests in 14.752s
OK (skipped=21)
Coverage: 100%
```

Command: `LOGBOOK_PYTHON=/tmp/logbook-oversize-venv/bin/python
LOGBOOK_DIFF_COVER=/tmp/logbook-oversize-venv/bin/diff-cover scripts/quality-gate`.
The temporary environment matches the previous installed dependency versions;
an initially resolved newer linter/dependency set caused unrelated check failures.
The 21 skips require an isolated graph in the general run. Two selected graph
tests were run separately against the guarded fixture below.

A separate read-only run generated all 71 real absent-job plans in private
temporary files, validated them structurally with the new 5,000-record limit,
and deleted the files. It printed only aggregate counts:

```text
candidate_structural_check_passed: 71
discarded_status_preserved: 3
largest_source_plan_records: 3923
no_owner_policy_or_graph_write
```

This used a synthetic release identity for structural checks, not a production
authorization. Source and graph sets were sampled separately. SQLite backup may
touch ordinary WAL/shared-memory companion files; no application ledger writes
were requested. Identifiers, transcript text, and plan contents were not logged.

Two actual database tests ran against a disposable Memgraph 3.10.1 container on
Odin with network mode `none`, one CPU, 512 MiB, no host bind mounts, and three
fresh anonymous image volumes. The client entered only that network namespace,
dropped root to `bernd`, verified that only loopback existed, required an empty
graph, and seeded the unique fixture marker required by destructive tests.
Source/test archive SHA-256:
`4ade30b26af728873a8acef45f1ba11bcdfa6d90fb2b425f54cd6101ede41326`.

```text
test_large_source_plan_progresses_and_each_window_can_be_reversed ... ok
test_bounded_batch_records_recovery_and_preserves_other_data ... ok
Ran 2 tests in 0.217s
OK
remaining_nodes_in_disposable_graph: 1
```

The first test wrote 601 nodes and 600 relationships through windows of
500/500/201 records, verified each batch manifest, then reversed the batches in
reverse order. The second checked unrelated-data preservation. Only the fixture
marker remained. The container, its anonymous volumes and temporary client files
were removed; production Memgraph and the forwarding service remained active,
and the agent VM remained stopped.

## Discard semantics and remaining boundaries

`docs/dead-letters.md` defines discard as removal from the pending list, retaining
the note and audio. The source graph builder includes the ledger's status.
All three real discarded-job plans retained `dead_letter_discarded`; this change
does not reactivate those jobs or alter that existing retention behavior.

This is candidate code and fixture proof. It is not production repair, a protected
cross-host activation rehearsal, or proof of independent source authority. The
host administrator can control the test environment. Independent administrator
and source authority, the exact protected executor upgrade, and fresh final
policy approval remain required before production activation.

## Changed files and evidence

- `src/logbook/graph_repair_policy.py`: re-exports shared, versioned policy bounds.
- `src/logbook/standing_graph_policy.py`: validate the explicit source-plan bound.
- `src/logbook/operator_bundle_bootstrap.py`: preserve protected installation.
- `src/logbook/named_repair_dispatcher.py`: keep version 3 on the fixed executor.
- `src/logbook/partial_graph_discovery.py`: inspect complete bounded source plans.
- `src/logbook/repair_window_worker.py`: select one bounded missing-record batch.
- Tests in the corresponding policy, installer, dispatcher, discovery, window,
  and guarded-repair files cover these paths.
- `Changelog.md`, `.codex/backlog.md`, and `.codex/status.md` record capability and
  the next action. Git review supplies the exact source diff.

## Standalone installer correction

Review after the initial signed publication found that the standalone named
installer imported the new shared helper through `logbook`, which may not be
installed when it runs. Production repair remained inactive. Move ownership of
the helper and constants into the standard-library-only installer, and re-export
them from the shared module. This avoids duplicate policy parsing and preserves
the standalone entry point. A regression test copies only that installer into a
temporary directory and executes its version-3 validation with Python `-I -S`,
which disables project paths and site packages. The test failed before the fix
and passed afterward; 30 related installer/policy tests passed. Require the
corrected signed release before protected installation or activation.
