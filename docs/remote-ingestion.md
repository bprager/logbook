# Remote Sony ingestion, v1.3.0 design

Status: Implemented and tested on `feature/remote-sony-ingestion`; physical field
acceptance and production deployment require approval. Date: 2026-10-09.

See [automated acceptance evidence](remote-acceptance.md),
[installation and recovery](remote-installation.md), and the
[execution plan](plans/2026-10-09-remote-ingestion.md).

## Goal

Preserve zero-action Sony ICD-PX370 USB ingestion on mimir and enable the same plug-in workflow on either MacBook Air while traveling. All transcription, diarization, routing, canonical Obsidian publication and Memgraph processing remain on mimir/odin.

## Current baseline

Existing `StartOnMount` runner, SQLite checksum job ledger, `process-mounted-recorder`, one-week guarded cleanup, Odin worker and Obsidian vault sync are retained. Existing launchd recorder access uses a stable `LogbookMountRunner.app` identity to handle macOS removable-volume TCC permissions. The new Mac agent must use an equivalent stable signed/packaged identity with documented permission onboarding.

## Design

1. Mac agent discovers a validated Sony volume, enumerates eligible audio files, ignores AppleDouble/sidecars, and snapshots only stable files.
2. Compute SHA-256 over complete bytes, plus size. Use a local SQLite outbox keyed by hash, with atomic spool writes, fsync and crash recovery. Cache path/size/mtime/hash as an optimization only, never as authority.
3. Authenticate over private VPN (prefer existing VPN initially) to a dedicated mimir ingest interface. Never expose the existing loopback OpenClaw action API or reuse its action token. Bind ingest listener to a private interface, require device-specific scoped credentials, TLS or VPN transport, payload limits, rate limits, and auditable device IDs.
4. API contract: `POST /ingest/recordings/check` (hash, size, non-sensitive recorder metadata) returns `present|missing|uploading`; `POST /ingest/uploads` allocates idempotent upload session; chunked upload with verified offsets and bounded size; `POST /ingest/uploads/{id}/complete` atomically validates SHA-256, fsyncs and renames to immutable inbox, then transactionally registers the canonical ledger recording. Status/ack includes durable receipt ID. Reconcile incomplete commit via hash lookup after network loss. Chunk transfer uses `PUT /ingest/uploads/{id}` with `X-Offset` and `X-Chunk-SHA256`. Device telemetry uses `POST /ingest/status`.
5. Deduplicate on mimir with a database UNIQUE content hash and transaction-safe claim, including concurrent clients. Existing records may have been pruned locally; never equate missing retained audio with an unseen recording. A known hash is never requeued merely because original audio was cleaned.
6. Trigger the existing downstream pipeline for new committed audio, without requiring Sony mount. Preserve source recording timestamps, timezone metadata and late-arrival canonical log rebuild behavior. Retry downstream independently of uploads. Persistent remote-delivery rows retain graph failures and retry delays; an idle worker still checks pending vault synchronization.
7. Mac agent automatically retries with bounded exponential backoff, supports offline use and restart, and never deletes or modifies Sony files. Two MacBooks share the server registry; local caches are optional accelerators.
8. Direct mimir USB ingestion retains existing one-week guarded recorder pruning after finalized processing and vault sync. Remote receipt alone never makes recorder audio eligible for pruning. Pruning runs only on mimir, validates exact source path, device identity and checksum, and records audit evidence. Remote-origin recordings may be pruned later when the Sony is attached to mimir, subject to the same gates.
9. Preserve current local audio retention and saga non-audio backup policy. A remote upload acknowledgment means durable receipt on mimir, not necessarily a second independent audio backup; explicitly decide durability/backup policy before production and document any risk window.
10. Expose redacted ingest status, device last-seen, pending bytes, failures and retry counts through existing observer interfaces; no raw audio paths, transcripts or credentials.

## Acceptance

- Connecting Sony to either MacBook launches ingestion without manual application steps after one-time setup and permissions.
- Previously ingested recordings are skipped across both MacBooks and mimir, even after recorder or local copied audio was pruned.
- Offline disconnect, interrupted upload, simultaneous upload, reboot, and lost completion response do not lose files or produce duplicate ledger jobs or notes.
- New remote audio reaches existing Odin/Obsidian/Memgraph pipeline and respects late-arrival rebuilding.
- Remote MacBooks never prune Sony recordings. Direct mimir mount retains existing audited one-week safe pruning.
- Unauthorized requests and wrong removable volumes are rejected; credentials are per-device and revocable.
- Automated tests cover hash mismatch, crash windows, malicious paths, TCC denial, low disk space, retries, ledger migration and cleanup safety.
- No production deployment, version bump, or v1.3.0 tag before passing `scripts/quality-gate`, integration testing and operator approval.
