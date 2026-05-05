# Lessons Learned

This document captures operational lessons from live Logbook runs. Keep it current when a
production incident, near miss, recovery, or release hardening step teaches something that future
operators or Codex sessions should not have to rediscover.

## Maintenance Rules

- Add a dated entry whenever live behavior differs from expected design, even if the fix is small.
- Link the lesson to concrete evidence: ledger job IDs, vault paths, command outputs, or release notes.
- Prefer prevention guidance over blame. A useful entry says what changed and how to verify it.
- Keep secrets, bearer tokens, source audio paths inside notes, and private transcript content out of this file.
- Update this document alongside `Changelog.md`, `.codex/status.md`, and relevant runbooks.

## 2026-05-05: Mount Triggers Must Run the Whole Bounded Pipeline

**Incident:** The Sony recording `260505_0919_01.mp3` was present on the recorder but absent from
the SQLite ledger, transcript store, Obsidian vault, and Memgraph. The installed StartOnMount job
only ran read-only `recorder-discover`, so a mount event could report files without copying or
processing them.

**Fix:** `local.logbook.recorder.mount-probe` now runs `logbook process-mounted-recorder --env .env`.
That bounded command copies discovered audio, transcribes through `odin`, diarizes meetings, routes
generated notes into Obsidian, marks pushed artifacts as vault-synced, and syncs the proof graph.
It still prints `delete_audio=no` and `delete_recorder_audio=no`; retention cleanup remains separate.

**Verification:** Re-running `process-mounted-recorder` after recovery exits cleanly with
`route_transcripts=skipped_no_candidates`. Launchd now shows `process-mounted-recorder` in the
StartOnMount job arguments.

## 2026-05-05: Removable-Volume Access Errors Need Controlled Failures

**Incident:** macOS intermittently denied access to
`/Volumes/IC RECORDER/REC_FILE/FOLDER01`, producing a Python traceback from `Path.iterdir()`.

**Fix:** Recorder enumeration now raises `RecorderAccessError`, and copy/discovery commands report
an actionable warning plus a nonzero exit. This makes launchd logs useful and prevents silent
failure modes.

**Verification:** Regression tests cover permission-denied discovery and copy behavior.

## 2026-05-05: Live Meeting Jobs Need Long HTTP Timeouts

**Incident:** The client timed out after 30 seconds while transcribing the 33 MB May 5 meeting file.
Jobs 50 and 51 completed, but job 52 was left at `copied` until the command was retried.

**Fix:** The default `HttpOdinClient` timeout is now 900 seconds so long ASR and diarization jobs can
complete without being abandoned by the caller.

**Verification:** Regression coverage asserts the default timeout is at least 900 seconds.

## 2026-05-05: Pyannote Diarization Is More Reliable on Normalized WAV

**Incident:** `pyannote.audio` failed while cropping directly from MP3 near the end of the May 5
meeting, raising a sample-count mismatch.

**Fix:** The `odin` worker now normalizes non-WAV audio to mono 16 kHz WAV with `ffmpeg` before
calling pyannote. The patched worker was deployed to `odin` and restarted through the
`logbook-odin-worker.service` user systemd unit.

**Verification:** Job 52 diarized successfully and produced the Obsidian meeting note
`30 - Meetings/2026/05-May/2026-05-05T09-19-00-job-000052-meeting.md`.

## 2026-05-05: Obsidian Workspace State Must Not Block Vault Writes

**Incident:** A dirty tracked `.obsidian/workspace.json` blocked `git pull --ff-only`, preventing
generated notes from being committed after routing succeeded locally. A second vault-stage failure
occurred because `git add` included a generated root (`20 - Notes`) that did not yet exist.

**Fix:** The vault workflow now stashes only `.obsidian/workspace.json` before sync, marks it
`skip-worktree`, and ensures all generated-note roots exist before staging. The mount pipeline can
also resume by committing pending generated vault changes even when ledger jobs are already routed.

**Verification:** Regression tests cover workspace stashing and generated-root creation. The vault
commit `796346c Update Logbook generated notes from mounted recorder` was pushed, and jobs 51 and
52 were marked vault-synced.

## 2026-05-05: Recovery Must Handle Clean Vault, Unsynced Ledger States

**Incident:** A bounded mount-processing command can crash after generated notes are committed and
pushed but before `vault_synced_at` and memory evidence are refreshed. On retry, there may be no
routing candidates and no dirty vault files, but the ledger still needs final proof marking.

**Fix:** `process-mounted-recorder` now runs guarded vault-sync marking even when there are no
routing candidates. It syncs Memgraph evidence for newly marked jobs and fails if any final generated
path is not present in pushed vault `HEAD`.

**Verification:** Regression coverage includes
`test_mount_processing_recovers_clean_pushed_job_before_vault_sync_mark`.

## 2026-05-05: Large Memgraph Syncs Can Outlive Useful Evidence

**Incident:** Full proof-graph syncs for large meeting jobs can run long enough that the operator
needs to check live graph evidence directly.

**Fix:** For job 52, the live Memgraph query confirmed `logbook:job:000052` and its generated note
were present. The lingering standalone sync process was terminated after evidence was confirmed.

**Verification:** Memgraph query:

```cypher
MATCH (j:LogbookJob {id:'logbook:job:000052'})
OPTIONAL MATCH (j)-[:GENERATED]->(n:GeneratedNote)
RETURN j.id, j.status, j.recorded_at, n.note_path;
```

returned the expected `meeting_written` job and meeting note path.
