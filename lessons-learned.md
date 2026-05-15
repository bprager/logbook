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

## 2026-05-12: Launchd Needs A Stable App Identity For Recorder Privacy Access

**Incident:** A mounted-recorder audit found 11 new Sony files from May 10 through May 12 that were
not in SQLite or Obsidian. Interactive `ingest-dry-run --env .env` could read
`/Volumes/IC RECORDER/REC_FILE/FOLDER01`, but the `local.logbook.recorder.mount-probe` LaunchAgent
repeatedly logged `Operation not permitted`. The user TCC database showed removable-volume access
granted to iTerm, while launchd was starting Apple CommandLineTools Python directly as
`com.apple.python3`.

**Fix:** Manual recovery processed recorder jobs `79`-`89`, producing seven consolidated log
entries, two meeting notes, and two dead letters, all pushed to the Obsidian vault. Launchd
packaging now renders `LogbookMountRunner.app` and the mount probe opens that app via LaunchServices
instead of executing bare Python. The app has a stable bundle identifier
`ws.prager.logbook.mount-runner`, so macOS privacy approval can target the actual background mount
processor.

**Verification:** The manual run copied 11 files, transcribed 11, diarized 2, routed 11, and
consolidated May 10, May 11, and May 12 daily logs. `mark-vault-synced --env .env` then reported
`already_synced_count=78` and `blocked_count=0` after restoring missing dead-letter notes for jobs
`51`, `54`, `55`, and `57`. Future rollout verification must grant Full Disk Access or
removable-volume access to `LogbookMountRunner.app`, then kickstart the mount probe and confirm the
mount log no longer reports `Operation not permitted`.

## 2026-05-10: Mounted Processing Must Finalize Logs After Routing

**Incident:** A recorder check found 13 new MP3 files still unknown to the ledger and three earlier
log jobs (`50`, `59`, and `60`) stuck at `inbox_written`. The mounted-recorder command routed log
entries into `10 - Logs/00 - Inbox`, then attempted vault-sync marking without first running daily
log consolidation. Because `inbox_written` is not a final sync state, those jobs could not pass the
retention gate. The installed launchd job also continued to hit removable-volume `Operation not
permitted` errors, which caused it to exit before finishing any already-local recovery work.

**Fix:** `process-mounted-recorder` now runs canonical daily-log consolidation after routing and
also during no-routing-candidate recovery runs. It also continues transcription, routing,
consolidation, vault-sync marking, and memory sync for local work even when recorder copy/discovery
fails, while still returning nonzero for the failed mount-copy stage. Manual recovery processed the
13 new files as jobs `66`-`78`, consolidated jobs `50`, `59`, `60`, `67`-`73`, `75`, and `78`,
diarized meeting jobs `66`, `74`, and `76`, and routed job `77` to dead letters.

**Verification:** `ingest-dry-run --env .env` reported `new_count=0` and `known_count=34`.
`process-mounted-recorder --env .env` immediately reran idempotently with `copy_skipped_count=34`,
`routing_candidate_count=0`, and `consolidation_candidate_count=0`. The vault commits
`afb4b6b Update Logbook generated notes from mounted recorder` and
`cb8313c Update Logbook daily logs from mounted recorder` are pushed. Follow-up proof checking
exposed four stale `vault_synced_at` records with missing vault paths; jobs `51`, `55`, `57`, and
`64` were restored through the normal routing/vault workflow, ending at vault commit `bab44d8`.
The vault working tree is clean, `mark-vault-synced --env .env` reports `blocked_count=0`, and
`memory-graph-health --env .env` reports `status=ok` with 4,726 nodes and 12,320 relationships
after pruning stale managed graph entries.

## 2026-05-10: Vault-Sync Proof Must Recheck Already-Synced Jobs

**Incident:** `mark-vault-synced --env .env` showed jobs `51`, `55`, `57`, and `64` as
`already_synced` even though their required generated note paths were missing from pushed vault
`HEAD`. The command listed `missing_in_vault_head` blockers but did not count those jobs as blocked
because `vault_synced_at` took precedence over proof validation.

**Fix:** Vault-sync proof now evaluates blockers before accepting an existing `vault_synced_at`.
Previously marked jobs with missing pushed paths are reported as blocked until the generated notes
are restored or the ledger state is explicitly repaired.

**Verification:** Regression coverage now checks that a previously synced job with a missing vault
path is blocked. Jobs `51`, `55`, `57`, and `64` were restored and pushed, and the dry-run proof
now reports `already_synced_count=67` and `blocked_count=0`.

## 2026-05-06: StartOnMount Can Fire Before Recorder Access Settles

**Incident:** The May 6 StartOnMount run saw the Sony recorder but could not enumerate
`/Volumes/IC RECORDER/REC_FILE/FOLDER01`, logging `Operation not permitted`. The ledger remained at
job 58 from May 5, so the seven new recorder files (`260505_1501.mp3` and `260506_0804.mp3` through
`260506_1020.mp3`) never reached Obsidian until manual recovery.

**Fix:** Manual `process-mounted-recorder` recovery copied all seven files, transcribed all seven,
diarized four meetings, routed all seven notes, and pushed the vault commit
`9352837 Update Logbook generated notes from mounted recorder`. The mount-copy retry window now
covers 24 attempts at 15 seconds each, giving slow removable-volume access up to six minutes to
settle before launchd gives up.

**Verification:** `ingest-dry-run` reported `new_count=0` and `known_count=21` after recovery. Jobs
61-64 are meeting notes under `30 - Meetings/2026/05-May/`, job 65 is a dead letter, and jobs 59-60
are log inbox entries.

## 2026-05-06: Recovery CLI Should Not Require Server Extras

**Incident:** Running `python3 -m logbook.cli recorder-discover` from the Homebrew Python 3.14
operator shell failed before parsing the command because `logbook.cli` imported the `odin_worker`
FastAPI app at module load time.

**Fix:** The `odin_worker` import is now lazy inside `serve-odin-worker`, so recorder discovery,
ingest dry-runs, and recovery commands can run from lighter Python environments. Serving the worker
still returns a controlled configuration error if FastAPI is missing.

**Verification:** Regression coverage imports `logbook.cli` while simulating missing FastAPI and
expects success.

## 2026-05-06: Memgraph Sync Needs Both The Project Runtime And Logbook Indexes

**Incident:** The May 6 recovery wrote Obsidian notes but post-route Memgraph sync failed under
`/usr/bin/python3` with `neo4j package is required for --execute`. Replaying sync with the project
`.venv` exposed a second issue: without Logbook label `id` indexes, relationship writes and repair
queries were slow against the much larger shared Memgraph database.

**Fix:** The live launchd plist already points at `.venv/bin/python`, and the missed jobs were
replayed through that runtime. Graph sync now creates label `id` indexes for planned Logbook node
labels before writes, treats transient Memgraph index DDL storage-lock errors as nonfatal, and
counts/repairs only managed Logbook relationships with explicit `id` properties.

**Verification:** Jobs 59-65, plus historical partial jobs 52 and 58, were replayed into Memgraph.
`memory-graph-health --env .env` returned `status=ok` with 3,705 planned/live nodes and 9,664
planned/live relationships.

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

**Fix:** Recorder enumeration now raises `RecorderAccessError`, copy/discovery commands report an
actionable warning plus a nonzero exit, and the mounted-recorder processor retries recorder
discovery/copy briefly before treating the mount as failed. This makes launchd logs useful and
prevents transient removable-volume readiness or permission timing from dropping a batch.

**Verification:** Regression tests cover permission-denied discovery, dry-run handling, and retry
recovery after a transient recorder access error.

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

## 2026-05-05: Dead Letters Need a Meeting Rescue Path

**Incident:** `260505_1135.mp3` was routed to `99 - Dead Letters` even though the operator later
identified it as a meeting. The existing dead-letter manager could rescue items only into daily-log
inbox entries.

**Fix:** Dead-letter management now has a dry-run-first meeting rescue path:
`scripts/manage-dead-letters --env .env --action rescue --job-id <id> --target meeting`. Execution
diarizes the recording as a meeting, writes the meeting note, records an audit action, clears stale
vault-sync state, and removes the obsolete dead-letter Markdown only after the meeting note exists.

**Verification:** Regression tests cover dry-run safety and successful meeting rescue with
dead-letter note removal.

## 2026-05-05: Large Memgraph Syncs Can Outlive Useful Evidence

**Incident:** Full proof-graph syncs for large meeting jobs can run long enough that the operator
needs to check live graph evidence directly. During the 11:43 batch, jobs 53-58 reached vault sync,
but the foreground command lingered in graph sync until it was terminated after ledger and vault
evidence were already durable.

**Fix:** `process-mounted-recorder` now runs Memgraph sync as bounded per-job subprocesses. A graph
timeout is reported as partial graph sync, but it does not block already completed copy,
transcription, diarization, Obsidian routing, or vault-sync marking.

**Verification:** Memgraph queries confirmed generated-note evidence for jobs 52 and 58. Regression
coverage includes a timeout case for mounted-recorder graph sync.

Example query:

```cypher
MATCH (j:LogbookJob {id:'logbook:job:000052'})
OPTIONAL MATCH (j)-[:GENERATED]->(n:GeneratedNote)
RETURN j.id, j.status, j.recorded_at, n.note_path;
```

returned the expected `meeting_written` job and meeting note path.
