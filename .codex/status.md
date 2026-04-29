# Status

Updated: 2026-04-29

## Current Focus

Obsidian CLI vault workflow around test-vault routing.

## Active Request

- Keep `.env` local and excluded from the remote repo.
- Route fake transcripts by spoken prefix into a test vault path without touching the real Obsidian vault.
- Keep source audio in place; no recorder-side or local audio deletion in this step.
- Add preflight and workflow boundaries for Obsidian CLI sync/write behavior.

## Progress

- [x] Created `.codex/status.md`.
- [x] Reviewed `prager.ws` repository structure.
- [x] Identified relevant hosts, networking, service deployment, and secrets patterns.
- [x] Assessed communication options for Mac Mini, `odin`, Obsidian vault, and OpenClaw.
- [x] Proposed architecture and implementation sequence.
- [x] Updated README for GitHub.
- [x] Prepared `0.1.0` release notes.
- [x] Committed and pushed `0.1.0`.
- [x] Verified Sony ICD-PX370 is mounted as `IC RECORDER` at `/Volumes/IC RECORDER`.
- [x] Verified recordings folder `/Volumes/IC RECORDER/REC_FILE/FOLDER01` exists, is readable, is writable, and contains MP3 files.
- [x] Added local `.env` normalization with `SONY_RECORDER_DEVICE_PATH=/dev/disk4s1` and `SONY_RECORDER_MOUNT_PATH="/Volumes/IC RECORDER"`.
- [x] Noted that Obsidian CLI binary `obsidian` is not currently on `PATH`.
- [x] Corrected existing recorder files from 2025 to 2026 filenames and mtimes.
- [x] Added `logbook recorder-discover --env .env` read-only CLI.
- [x] Added config parsing, recorder validation, Sony filename parsing, sidecar filtering, and tests.
- [x] Ran recorder discovery dry-run against the connected recorder.
- [x] Added SQLite ledger schema and idempotent checksum-based discovery records.
- [x] Added `logbook ingest-dry-run --env .env`.
- [x] Added `--record-discovery` mode to write local checksum records without copying or deleting audio.
- [x] Recorded 17 connected-recorder files in `/Users/bernd/VoiceIngest/voice_ingest.sqlite`.
- [x] Added `logbook copy-discovered --env .env`.
- [x] Copied 17 recorder MP3 files into `/Users/bernd/VoiceIngest/inbox`.
- [x] Verified repeat copy run skips all 17 already-copied files.
- [x] Added typed `odin` submit/result models.
- [x] Added fake `odin` client and HTTP client boundary.
- [x] Added `logbook fake-transcribe-copied --env .env`.
- [x] Wrote 17 fake transcript JSON files under `/Users/bernd/VoiceIngest/transcripts`.
- [x] Updated ledger status to `transcribed|17`.
- [x] Added deterministic prefix classifier, Obsidian path builders, Markdown renderers, and test-vault routing.
- [x] Ran route-transcripts against `/Users/bernd/VoiceIngest/test-vault`.
- [x] Wrote 17 log inbox Markdown notes from fake transcripts.
- [x] Verified generated test-vault Markdown does not contain `.mp3`, local processing paths, or recorder paths.
- [x] Added Obsidian CLI configuration parsing and tracked `.env.example` placeholders.
- [x] Added `logbook vault-preflight --env .env`.
- [x] Added vault workflow locking and configurable sync/status/commit/push command templates.
- [x] Added optional `--vault-workflow preflight|obsidian` routing wrapper.

## Notes

- OpenClaw runtime owner on this host remains `clawdbot`; do not run OpenClaw services under `bernd`.
- Use memgraph MCP for dependency and graph queries.
- `prager.ws` documents `fenrir` as edge/jump host, `odin` as main internal/observability host, `saga` as preferred storage, `qnap` as legacy storage, and `mimir` as OpenClaw/control-plane host.
- `odin` already runs the observability stack; Prometheus/Loki are internal-only, Grafana is bound on `192.168.1.3:3000` and reverse-proxied by `fenrir`.
- `mimir` is the right OpenClaw host and OpenClaw should use scoped APIs/credentials, not broad shell access.
- The PRD's Mac Mini is confirmed to be `mimir`.
- Recommended communication model: `mimir` keeps local SQLite/processing/vault writes, submits async jobs to `odin` over direct internal HTTP, OpenClaw reads/requests through a loopback status/action API, Prometheus scrapes health/metrics, and `saga` is backup/archive only.
- MVP auth decision: scoped bearer tokens in `.env`; separate tokens for `odin` jobs, Logbook read access, and Logbook action access.
- Obsidian vault: `https://github.com/bprager/obs-vault.git`; use Obsidian CLI to access and update it.
- Audio retention: do not link audio in Obsidian; delete local and Sony-recorder source audio after 24 hours once processing and vault sync are confirmed.
- Local `.env` placeholder created and ignored by git for tokens, Obsidian CLI settings, `odin` API config, Sony recorder mount details, and retention settings.
- Detailed plan captured in `.codex/architecture-plan.md`.
- `0.1.0` is the first minor planning release and is tagged as `v0.1.0`.
- Recorder operational check on 2026-04-29 found 17 MP3 files in the configured folder.
- Recorder filenames and mtimes have been corrected to 2026.
- Retention cleanup should still be based on Logbook ledger timestamps rather than recorder mtimes or filename dates.
- Dry-run discovery reports one filename/mtime minute mismatch: `260427_1351.mp3` parses as 13:51 while filesystem mtime is 13:52:00.
- Verification passed with `PYTHONPATH=src python3 -m unittest discover -s tests -v`.
- Recorder dry run passed with `PYTHONPATH=src python3 -m logbook.cli recorder-discover --env .env`.
- Initial ingest dry run reported 17 new files, then `--record-discovery` wrote 17 local ledger rows, and the repeat dry run reported 17 known files.
- Copy run reported 17 copied, 0 failed; repeat copy reported 0 copied, 17 skipped, 0 failed.
- SQLite ledger currently reports `copied|17`.
- Recorder still contains 17 MP3 files; no recorder-side deletion was performed.
- Fake `odin` pass wrote local placeholder transcripts and updated the ledger to `transcribed|17`.
- Test-vault routing updated the ledger to `inbox_written|17`.
- Current implementation still does not call real `odin`, delete audio, or write to the real Obsidian vault.
- Routing implementation started at 2026-04-29T16:38:17Z; target output is `/Users/bernd/VoiceIngest/test-vault`, not the real Obsidian vault.
- Test-vault route command completed with `routed_count=17`, `log_count=17`, `dead_letter_count=0`, and `failed_count=0`.
- Obsidian workflow implementation started at 2026-04-29T16:46:05Z.
- `obsidian` is currently not discoverable on `PATH`; the real vault local path is also not present on this host path yet.
- Live `vault-preflight` against `.env` completed safely with no writes and reported `operational=no` because `obsidian` is missing and `/Users/bernd/Obsidian` does not exist.
- Verification passed with `PYTHONPATH=src python3 -m unittest discover -s tests -v` after adding the vault workflow: 24 tests OK.
- Verification passed with `python3 -m py_compile src/logbook/*.py` and `git diff --check`.
- Rechecked at 2026-04-29T16:53:23Z: `obsidian-cli` is available at `/opt/homebrew/bin/obsidian-cli`; `obsidian` is still not on `PATH`.
- `obsidian-cli print-default` reports no configured default vault yet, and `/Users/bernd/Obsidian/obs-vault` is not present.
- Implemented optional `obsidian-cli create` writer at 2026-04-29T17:02:34Z.
- Updated local `.env` to use `OBSIDIAN_CLI_BIN=/opt/homebrew/bin/obsidian-cli` and `OBSIDIAN_VAULT_NAME=obs-vault`.
- Scratch probe showed `obsidian-cli` cannot write to arbitrary scratch folders until Obsidian has registered the vault; temporary scratch CLI state was removed.
- Verification passed with `PYTHONPATH=src python3 -m unittest discover -s tests -v` after adding the writer: 25 tests OK.
- User reported GitHub token rotation at 2026-04-29T17:11:31Z.
- Non-writing readiness check after token rotation: `obsidian-cli` is available, but `/Users/bernd/Obsidian/obs-vault` is still missing and `obsidian-cli list --vault obs-vault` reports the vault is not registered in Obsidian.
- Cloned `https://github.com/bprager/obs-vault.git` to `/Users/bernd/Obsidian/obs-vault` at 2026-04-29T17:14:04Z.
- The cloned vault is on `main...origin/main` and includes a `.obsidian` directory.
- Tightened `vault-preflight` to verify `obsidian-cli list --vault obs-vault` before considering the CLI writer operational.
- Current `vault-preflight` now fails on `obsidian_vault_registered`; the Obsidian desktop app/config is not present on this host, so the vault still needs to be registered/opened in Obsidian before `obsidian-cli create` can work.
- Verification passed after tightening preflight: 27 tests OK.
- Rechecked after user registered the vault at 2026-04-29T17:17:12Z: `vault-preflight` is now `operational=yes` and `obsidian-cli list --vault obs-vault` succeeds.
- Opening/registering the vault modified `/Users/bernd/Obsidian/obs-vault/.obsidian/workspace.json`; treat this as Obsidian app state, not a Logbook-generated note.
- `obsidian-cli create` smoke test wrote and printed `Logbook Sandbox/CLI Smoke Test`, then deleted the disposable note successfully.
- Added `--job-id` routing guard for one-job writes and verified it in tests.
- First real Obsidian vault Logbook note write completed at 2026-04-29T17:23:25Z for ledger job 17 using `--writer obsidian-cli --job-id 17`.
- Generated vault note path: `/Users/bernd/Obsidian/obs-vault/10 - Logs/00 - Inbox/2026/04-April/2026-04-29/2026-04-29T08-21-00-job-000017-log-entry.md`.
- Verified the generated note through `obsidian-cli print` and filesystem read; no `.mp3`, local processing path, or recorder path appears in the note.
- Real vault git status now shows `.obsidian/workspace.json` modified and `10 - Logs/` untracked.
- Verification after first real-vault write: 29 tests OK, `py_compile` OK, and `git diff --check` OK.
- Committed the generated Obsidian note in `/Users/bernd/Obsidian/obs-vault` at 2026-04-29T17:27:45Z: `b531a7c Add Logbook inbox entry for job 17`.
- The vault repo is now ahead of `origin/main` by 1 commit; `.obsidian/workspace.json` remains modified and uncommitted as Obsidian app state.
- Executed requested steps 1-6 on 2026-04-29:
  - Pushed `b531a7c Add Logbook inbox entry for job 17` to `origin/main`.
  - Left `.obsidian/workspace.json` unstaged as Obsidian app state.
  - Added `--include-routed` for controlled backfills of already-routed jobs.
  - Routed all 17 jobs into `/Users/bernd/Obsidian/obs-vault` through `obsidian-cli`; job 17 was overwritten with identical content, and the other 16 notes were created.
  - Committed and pushed `e234dc8 Add remaining Logbook inbox entries`.
  - Added Git vault workflow templates for pull, stage, status, commit, and push.
- Real vault now has 17 generated Logbook inbox notes committed and pushed; vault status is clean against `origin/main` except for uncommitted `.obsidian/workspace.json`.
- Verification after steps 1-6: 30 tests OK, `py_compile` OK, `vault-preflight` operational, and `git diff --check` OK.
- Committed the Logbook ingest/routing implementation in this repo as `c4e59be Implement Logbook ingest and Obsidian routing`.
- Added `consolidate-logs` and generated canonical daily logs from the 17 real inbox notes.
- Real vault daily log output:
  - `06 - Timestamps/2026/04-April/2026-04-27-Monday-Log.md` with 9 entries.
  - `06 - Timestamps/2026/04-April/2026-04-28-Tuesday-Log.md` with 6 entries.
  - `06 - Timestamps/2026/04-April/2026-04-29-Wednesday-Log.md` with 2 entries.
- Verified generated daily logs contain no `.mp3`, local processing path, or recorder path.
- Ledger now reports `consolidated|17` and records the canonical daily log path for each job.
- Committed and pushed the daily logs in the Obsidian vault as `db9d42f Add consolidated daily logs`.
- Vault remains clean against `origin/main` except for uncommitted `.obsidian/workspace.json`.
- Consolidation verification at 2026-04-29T17:50:44Z: 32 tests OK, `py_compile` OK, and privacy/path scan OK.
