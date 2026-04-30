# Architecture Plan

Updated: 2026-04-30

## Infrastructure Findings From `prager.ws`

Relevant host roles:

- `fenrir` (`192.168.1.2`): public edge, jump host, nginx reverse proxy.
- `odin` (`192.168.1.3`): main internal host, observability stack, best fit for GPU worker services.
- `saga` (`192.168.1.6`): preferred durable storage and backup target.
- `qnap` (`192.168.1.5`): legacy NAS; avoid new dependencies.
- `mimir` (`192.168.1.8`): OpenClaw/control-plane host, already running OpenClaw as `clawdbot`.

Confirmed: the PRD's Mac Mini is `mimir`.

## Recommended Host Placement

### Recorder Host / Mac Mini (`mimir`)

Responsibilities:

- Sony ICD-PX370 mount detection.
- Audio copy and dedupe.
- SQLite job ledger.
- Processing root under `~/VoiceIngest` or a configured local path.
- Obsidian vault writes.
- Log routing and consolidation.
- Narrow status/action API for OpenClaw.

Bind the OpenClaw-facing API to loopback first because OpenClaw runs on the same host as `clawdbot`.

### `odin`

Responsibilities:

- GPU job API.
- faster-whisper ASR.
- pyannote diarization.
- Health and metrics endpoint.
- Prometheus/Loki/Grafana observability stack.

The GPU job API should be internal-only and not reverse-proxied through `fenrir`.

### `mimir`

Responsibilities:

- OpenClaw as `clawdbot`.
- Recorder ingest daemon, because `mimir` is the Mac Mini.
- Read Logbook state through the status API.
- Request bounded actions through named endpoints only.

OpenClaw must not receive shell access to the ingestion daemon or `odin` worker.

### `saga`

Responsibilities:

- Backup target for audio archive, SQLite backups, config backups, and generated operational artifacts.

Do not put the hot SQLite ledger or active processing directory on SMB/AFP. Keep runtime state on local disk and back it up.

### `fenrir`

Responsibilities:

- Public edge for dashboards or human-facing status if explicitly desired.

`fenrir` should not sit in the Mac Mini to `odin` transcription path.

## Recommended Communication Topology

```text
Sony recorder
  -> mimir launchd trigger
  -> mimir ingest daemon + SQLite
  -> direct LAN HTTPS/API call to odin GPU worker
  -> mimir stores transcript, routes, and writes Obsidian Markdown through Obsidian CLI
  -> OpenClaw on mimir reads/requests through loopback status API
  -> Prometheus on odin scrapes health/metrics endpoints
  -> backups replicate to saga
```

## Protocol Choices

### Recorder Host To `odin`

Use a direct internal HTTP API with:

- idempotency keys based on recording checksum/job ID,
- async job submission,
- polling from the recorder host,
- explicit offline/queued/failed/succeeded states,
- scoped bearer token auth,
- request and response JSON schemas,
- audio upload by multipart file for MVP.

Prefer polling over callbacks for MVP. Polling keeps the recorder host in control and avoids opening inbound ports from `odin` to the Mac Mini.

Use bearer tokens for MVP rather than mTLS. The API is internal-only, `mimir` and `odin` are stable known hosts, and bearer tokens are easier to rotate through `.env`. Revisit mTLS only if the service is exposed beyond the trusted LAN or if additional clients appear.

### OpenClaw To Recorder Host

Use the PRD's narrow HTTP API:

- read endpoints for health, jobs, inbox, open date, latest consolidated log, and dead letters,
- write endpoints only for reprocess, rescue, and rebuild,
- audit every write action,
- separate read and action tokens,
- bind to loopback because OpenClaw is on the same host.

## Obsidian Vault Access

The vault lives at:

```text
https://github.com/bprager/obs-vault.git
```

Use the Obsidian CLI as the supported access/update mechanism. The Logbook service should work against a local checkout managed through that CLI rather than writing directly to a remote GitHub URL.

Recommended behavior:

- Clone/sync the vault through Obsidian CLI before a write batch.
- Write generated Markdown to the local vault checkout.
- Commit/push generated changes through Obsidian CLI or its configured workflow.
- Keep raw audio out of the vault.
- Keep source audio references out of generated Obsidian notes unless a filename/job ID is needed for audit.

## Audio Retention

New requirement: source audio should not be linked from Obsidian and should be deleted after 24 hours, including on the Sony recorder.

This supersedes the PRD MVP non-goal that avoided automatic deletion from the recorder. Implement it as a delayed, auditable cleanup stage, not as immediate post-copy deletion.

Required safety gates:

- Copy succeeded and checksum verified.
- Transcript and derived Markdown write succeeded.
- Vault sync/commit succeeded.
- Retention age is greater than 24 hours.
- Cleanup action is recorded in the SQLite ledger.
- Failed cleanup remains retryable and visible to OpenClaw.
- Prefer moving local audio to trash/quarantine before hard deletion when practical.

### Observability

Expose `/health` and `/metrics` on both the recorder host service and `odin` worker. Let Prometheus on `odin` scrape them over the LAN. Keep Prometheus and Loki internal-only as already documented in `prager.ws`.

### Storage And Backups

Keep active state local:

- SQLite ledger local to recorder host.
- Processing directories local to recorder host.
- Obsidian writes local or to a locally mounted vault only if atomic writes are reliable.

Back up to `saga` after state changes using SQLite backup semantics, not raw copies of a live WAL-mode database.

## Proof-Carrying Memory Graph

Strategic addition: turn Logbook from a recorder-to-Obsidian pipeline into a local, auditable memory engine.

Use Memgraph as a proof-carrying memory layer fed by the SQLite ledger, transcripts, diarization segments, routed notes, consolidated logs, and insight-review artifacts. The graph should store derived memory objects only when each object carries evidence back to the exact Logbook job, transcript segment, note path, timestamp, and source artifact that produced it.

Core graph objects:

- `LogbookJob`
- `TranscriptSegment`
- `GeneratedNote`
- `ActionCandidate`
- `Decision`
- `Topic`
- `Person`
- `Project`
- `SourceEvidence`

Core relationships:

- `(LogbookJob)-[:HAS_SEGMENT]->(TranscriptSegment)`
- `(LogbookJob)-[:GENERATED]->(GeneratedNote)`
- `(ActionCandidate)-[:SUPPORTED_BY]->(SourceEvidence)`
- `(Decision)-[:SUPPORTED_BY]->(SourceEvidence)`
- `(Topic)-[:MENTIONED_IN]->(SourceEvidence)`
- `(ActionCandidate)-[:RELATES_TO]->(Topic|Project|Person)`
- `(Decision)-[:SUPERSEDES|BLOCKS|ENABLES]->(Decision|ActionCandidate|Topic)`

Operating constraints:

- Dry-run by default; write to Memgraph only with explicit `--execute`.
- Do not store source audio paths in the graph.
- Store evidence references as job IDs, transcript segment offsets, generated note paths, and content checksums.
- Keep SQLite as the source of truth for processing state; Memgraph is the relational memory/query layer.
- Make graph sync idempotent by stable IDs derived from project, job ID, artifact type, and segment/action index.
- Query endpoints must be read-only first: open loops, unresolved actions, recent decisions, topic trail, and weekly change summary.

## Architecture Decisions To Confirm

1. Should `odin` expose the GPU API on an internal port directly, or behind an internal reverse proxy on `odin`?
2. Which exact Obsidian CLI command should be the supported sync/write path on this host?
3. What is the mounted volume name/path pattern for the Sony ICD-PX370 on `mimir`?
4. Should local source audio be moved to trash/quarantine at 24 hours, or hard-deleted after confirmed sync?

## Implementation Process

### Phase A: Planning Lock

- Confirm host identity and data locations.
- Freeze the state machine, path rules, and API contracts.
- Confirm Obsidian CLI command paths and workflow.
- Confirm Sony recorder mount path and deletion behavior.
- Document `mimir` as both OpenClaw host and recorder host in `prager.ws`.

### Phase B: Contract And Test Harness

- Define config schema.
- Define SQLite schema and migrations.
- Define `odin` job API schemas.
- Define Logbook status/action API schemas.
- Define audio cleanup state and safety gates.
- Build tests for path generation, frontmatter, classifier behavior, state transitions, and one-log-per-date.

### Phase C: Local Vertical Slice Without Audio

- Use fixture transcripts.
- Route log, category, meeting, and unknown text.
- Write Markdown to a test vault.
- Consolidate multi-day logs and late arrivals.

### Phase D: Mac Mini To `odin` Integration

- Build the `odin` worker behind the agreed API.
- Add a fake worker for tests and a real worker for `odin`.
- Validate offline queue behavior and idempotency.

### Phase E: USB Ingestion

- Add launchd mount trigger.
- Validate Sony recorder identity.
- Copy and checksum audio.
- Queue delayed cleanup for local source audio and recorder files after 24 hours.

### Phase F: OpenClaw And Observability

- Add status/action API.
- Add Prometheus metrics.
- Add scoped OpenClaw credentials.
- Add dashboards/alerts for queue depth, failures, `odin` offline, dead letters, and last consolidation.

### Phase G: Pilot And Hardening

- Run against a test vault.
- Replay synthetic and real sample recordings.
- Verify backup/restore.
- Only then point at the real vault.

### Phase H: Proof-Carrying Memory Graph

- Add dry-run-first Memgraph sync for ledger jobs, transcript segments, generated notes, action candidates, decisions, topics, people, projects, and source evidence.
- Add idempotent graph upserts with stable IDs and source checksums.
- Add read-only graph queries for open loops, unresolved action candidates, decision trails, topic trails, and weekly memory diffs.
- Expose graph-backed memory status through the existing API only after the sync path is covered by tests.

Status: baseline implemented in LGB-027 on 2026-04-30. The sync path is dry-run by default, Memgraph writes require explicit `--execute`, and `/memory/*` API endpoints expose bounded read-only memory queries for OpenClaw without shell access.

Follow-on status: LGB-028 adds durable action-candidate resolution state in SQLite, dry-run-first CLI resolution, and a token-protected API endpoint so resolved promises disappear from open-loop memory queries without mutating transcripts or generated notes.

Health status: LGB-029 adds a read-only graph drift check through CLI and FastAPI so local planned memory counts can be compared against live Memgraph before relying on graph-backed memory.
