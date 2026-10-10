# Remote Sony ingestion installation

Feature-branch implementation; **do not install on production without approval**.
Current production version remains 1.2.3. Both MacBook Air machines use the same
agent settings except for their private device credential and local directories.
The central service and worker run on mimir; no Odin or vault credentials belong
on either Mac. These are Logbook services, not OpenClaw services.

## Prepare each Mac

If an earlier checkout fails installation with “A second file is being added to
the wheel archive”, update the feature branch with `git pull --ff-only` and retry
`.venv/bin/python -m pip install .`. The duplicate observer-asset packaging rule
has been removed. There is no need to recreate the virtual environment or the
signing certificate for this failure.

1. Install this checkout and Python environment (`uv sync --extra dev` or the
   existing repository setup). Keep the checkout and environment at stable paths.
2. Copy `docs/examples/remote-agent.json` to a private settings directory. Replace
   the VPN address and inspect `/usr/sbin/diskutil info -plist '/Volumes/IC RECORDER'`
   to obtain the actual `VolumeUUID`, `VolumeName`, `MountPoint`, `Internal` and
   `BusProtocol`. The agent requires the matching external USB volume. An erased
   or replaced recorder requires explicit re-enrollment of its new UUID.
3. Configure the recorder clock's IANA timezone, even when the Mac travels. Sony
   filenames have no timezone. The agent preserves their local wall clock plus
   the configured zone. Ambiguous daylight-saving wall times remain ambiguous.
4. Place a separate random credential of at least 32 characters in `device.token`
   with mode `0600`. Never use an OpenClaw action token. Credential creation and
   installation require the operator's deployment approval; no example contains
   a usable secret. Keep settings, tokens and buffers outside the checkout.
5. Use a persistent code-signing certificate available in the local keychain
   (Apple Development or an operator-managed local Code Signing certificate).
   Keep the same certificate and app installation path across upgrades. Ad-hoc
   signing is deliberately rejected because rebuilt identities can lose TCC grants.
6. Generate the app and LaunchAgent, without loading them:

   ```sh
   .venv/bin/python -m logbook.remote_cli package-agent \
     --config '/absolute/settings/remote-agent.json' \
     --output '/absolute/LogbookRemotePackage' \
     --repo '/absolute/Logbook' \
     --signing-identity 'YOUR PERSISTENT SIGNING CERTIFICATE'
   ```

7. After approval, open `LogbookRemoteAgent.app` once while the recorder is
   attached. Grant Removable Volumes access when macOS asks. Check System
   Settings → Privacy & Security → Files and Folders if access is denied. The
   stable compiled parent opens the recorder directory before spawning Python,
   matching the production mount runner's TCC attribution approach. Validate on
   each Mac; an app signature alone does not grant access.
8. Copy only `local.logbook.remote-agent.plist` to `~/Library/LaunchAgents/`, then
   load it with `launchctl bootstrap gui/$(id -u) /absolute/path/to/the.plist`.
   It runs at login, on mounts and every 60 seconds. Login is required for this
   per-user LaunchAgent. Keep the package in its generated location.

The agent reads Sony files, snapshots stable audio into a local SQLite-backed
outbox, then uploads. It never runs recorder cleanup. Failed transfers persist
and retry after 30 seconds up to a bounded one-hour delay. Disconnecting Sony
or rebooting does not remove queued snapshots. Logs contain redacted counts and
failure codes; complete acknowledged snapshots remain in the buffer for now.
A simultaneous launch reports `busy` and leaves the current run alone.

## Prepare mimir

Copy `docs/examples/remote-server.json`, point it at the **existing** production
ledger, and keep the remote root on mimir's local durable filesystem. Do not
create a second job registry. The upload service binds only the chosen private
IP; firewall/VPN ACLs must allow only the enrolled MacBooks to reach port 8790.
HTTP is allowed only over an explicitly configured VPN or loopback. No public
listener, forwarded OpenClaw API, reverse proxy trust or action-token reuse is
needed. Certificate-verified HTTPS is supported by the agent if the operator
provides a private TLS terminator; do not disable certificate verification.

The mode-`0600` device registry maps device IDs to SHA-256 hashes of their
independent random bearer credentials:

```json
{
  "air-one": "SHA256_OF_AIR_ONE_RANDOM_TOKEN",
  "air-two": "SHA256_OF_AIR_TWO_RANDOM_TOKEN"
}
```

The sample values are placeholders and fail validation. Remove a device entry
and atomically replace the file to revoke it immediately, including already
allocated sessions. Duplicate tokens and insecure file permissions are rejected.
Do not reuse any existing production credential.

A manual staging invocation is:

```sh
.venv/bin/python -m logbook.remote_cli serve --config /absolute/settings/remote-server.json
```

The service has no transcription or deletion endpoints. It checks hashes,
allocates sessions, accepts bounded chunks and commits durable receipts. A receipt
means mimir has accepted responsibility, not that transcription or vault sync
has finished. Completed job hashes remain authoritative after audio retention.

## Buffer and recovery policy

- No automated Mac spool deletion in this implementation. Acknowledged copies
  remain available; monitor local disk space. Failed local snapshots leave Sony
  originals intact. Hidden interrupted snapshot directories are never uploaded.
- Active central sessions reserve disk capacity. Limits default to 2 GiB per
  recording, 1 MiB chunks, 20 GiB central intake storage and 1 GiB free reserve.
  Exhausted storage returns a safe retryable failure, never a false receipt.
- Upload allocation is idempotent per device/hash. Its offset identifies the
  committed byte prefix. Repeating completion after a lost acknowledgment returns
  the same receipt. The ledger registers one job across both Macs and direct USB.
- Interrupted/abandoned sessions remain for audit and recovery. Operators should
  inspect capacity before approving cleanup of stale temporary data. Automatic
  expiry and spool retention are intentionally not activated.
- The existing saga backup excludes audio. A receipt is not an independent audio
  backup. Keep both Sony originals and Mac snapshots until the operator chooses
  an explicit audio backup/retention policy before production. Losing mimir and
  all remaining source copies during this window can still lose audio.

## Automatic central processing and retention

Generate additional central plists without changing the existing production ones:

```sh
.venv/bin/python -m logbook.remote_cli package-server \
  --config /absolute/settings/remote-server.json \
  --env /absolute/Logbook/.env --repo /absolute/Logbook \
  --output /absolute/LogbookRemoteServerPackage
```

After deployment approval, install the two generated plists as LaunchAgents on
mimir: `local.logbook.remote-ingest` and `local.logbook.queued-worker`. The upload
service stays running. Every minute the worker calls `logbook process-queued`,
using the existing production environment and ledger. It transcribes, diarizes,
routes, consolidates, syncs the vault and updates Memgraph using the existing
pipeline. A failed downstream step retries independently of the Mac's upload.
A shared lock prevents the queued worker and mount runner processing concurrently;
the mount runner waits for an active worker, and another worker run skips.

Keep existing production mount and retention plists intact. For remote-origin
recorder cleanup, explicitly configure `SONY_RECORDER_VOLUME_UUID` on mimir with
the enrolled Sony volume UUID. On a later local attachment, Logbook hashes the
actual Sony file and records its path and identity against the canonical job.
Without that enrollment the remote recording remains recognized but is not
associated for recorder cleanup. Even with enrollment, only the existing cleanup
command on mimir may prune it, after the usual finalized-output, vault-sync and
one-week gates, and a fresh volume/path/checksum validation. A remote receipt
never enables pruning. Keep `LOGBOOK_AUDIO_RETENTION_HOURS=168` in production.

## Status, protocol and rollout checks

`logbook watch --env .env --json` and the existing authenticated
`GET /observer/snapshot` include `remote_ingest`: device last-seen, last-reported
pending bytes, retries, storage pressure, active uploads, downstream graph queue
and aggregate authentication/error counters. Plain `watch` includes a remote
summary. These fields never include token values, audio paths or transcript text.
An offline device's report can be stale; always inspect last-seen. Mac launch logs
also contain the persisted local pending counts and safe error codes.

| Operation | Contract |
| --- | --- |
| `POST /ingest/recordings/check` | Metadata JSON → missing, uploading with offset, or present with receipt |
| `POST /ingest/uploads` | Same JSON → idempotent session or existing durable receipt |
| `PUT /ingest/uploads/{id}` | Binary body, `X-Offset`, SHA-256 `X-Chunk-SHA256` → next durable offset |
| `POST /ingest/uploads/{id}/complete` | Empty body → receipt after full size/hash verification |
| `POST /ingest/status` | Only pending bytes, retries, allowlisted failure code and storage-pressure boolean |

Recording JSON fields are `sha256` (64 lowercase hex characters), `size` (bytes),
`filename` (MP3 basename only), `recorded_at` (naive ISO recorder wall clock), and
`timezone` (IANA zone). A competing device's successful commit settles duplicate
sessions and releases their reservations; only redundant transfer parts are
reclaimed. A full-file checksum rejection resets the session offset to zero.
The agent keeps its snapshot and retries allocation after any lost response.
The server limits each device to 128 active uploads and 600 requests/minute;
request bodies have a 30-second deadline. Limits are configurable.

Graph delivery failures retain an independent persistent retry record, including
a capped delay and a safe failure code. A scheduled worker checks that queue even
when no audio needs processing. Pending vault synchronization also counts as work
on otherwise idle runs. Upload acknowledgment is independent of either service.

Before approving activation on mimir and both MacBooks:

- [ ] Run `LOGBOOK_COVERAGE_COMPARE_REF=origin/main scripts/quality-gate` and review
  the feature branch. Back up the production SQLite ledger with the existing
  non-audio backup workflow before applying the additive migration.
- [ ] Confirm the private VPN addresses, firewall ACLs, separate device tokens,
  registry permissions and immediate revocation. Confirm the existing action API
  remains loopback-only and its token cannot authenticate to ingest.
- [ ] Confirm Xcode command-line tools (`cc`) and the persistent signing certificate
  are available on each Mac. Inspect the generated app with `codesign --verify
  --strict`, grant TCC once, and test a real plug-in event on each machine.
- [ ] Unplug the recorder while offline after snapshotting, reboot/login, restore
  VPN connectivity, and confirm the buffered recordings complete automatically.
- [ ] Attach the same test recordings to both Macs and then mimir. Confirm one job
  per hash and one final daily log per date, including a deliberately late entry.
- [ ] Interrupt an upload and lose a completion response; confirm restart resumes
  safely. Make Odin unavailable and then restore it; repeat with vault/graph sync.
- [ ] Verify recorder cleanup in dry-run mode first: no remote receipt bypasses
  finalized-output, vault-sync, one-week age, path, UUID or checksum checks.
- [ ] Choose and record the Mac spool retention and independent audio-backup policy.
  Existing saga backups remain non-audio; acknowledged spools are retained until
  a separately approved policy implements their cleanup.
- [ ] Obtain explicit approval for production deployment. Version promotion and
  the v1.3.0 tag are separate actions and have not been performed.

To disable remote ingestion, unload only the three new remote/worker LaunchAgents.
Do not unload the existing mimir mount runner or retention job. Keep outboxes,
upload metadata and the ledger for recovery. Schema migration is additive; older
code ignores the extra tables. Do not restore an old ledger over newly received
recordings without reconciling durable receipts first.
