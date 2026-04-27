# Research Notes

Updated: 2026-04-27

## Sources Consulted

- `docs/PRD.md` - source product requirements.
- [SYSTRAN faster-whisper README](https://github.com/SYSTRAN/faster-whisper) - supports `large-v3`, CUDA `float16`, VAD filtering, batched inference, and notes that transcription starts when segment generators are consumed.
- [pyannote speaker-diarization-3.1 model card](https://huggingface.co/pyannote/speaker-diarization-3.1) - requires `pyannote.audio` 3.1+, accepted Hugging Face conditions and token, can run on GPU, and outputs diarization annotations.
- [pyannote speaker-diarization-community-1 model card](https://huggingface.co/pyannote/speaker-diarization-community-1) - newer open-source pipeline with improved speaker assignment/counting and offline-use guidance.
- [launchd.plist man page](https://keith.github.io/xcode-man-pages/launchd.plist.5.html) - `StartOnMount` starts jobs when filesystems mount; `WatchPaths` is discouraged because filesystem events can be race-prone.
- [SQLite WAL documentation](https://www.sqlite.org/wal.html) - WAL allows readers during writes but has one writer, checkpoint behavior, and persistent `-wal`/`-shm` files that matter for backup/copy semantics.

## Assessment

The PRD is technically coherent for an MVP. The highest-risk areas are not raw transcription but data safety and lifecycle correctness: idempotent ingest, canonical path generation, late-arrival rebuilds, and bounded OpenClaw actions.

`faster-whisper` fits the requested `odin` worker design. The implementation should force complete segment consumption before marking ASR jobs complete, because the library returns lazy segment iterators.

`pyannote/speaker-diarization-3.1` matches the PRD, but `community-1` is now a credible upgrade candidate. Keep the model configurable and run a short spike before hardcoding the meeting diarization default.

For Mac Mini automation, prefer a small `launchd` `StartOnMount` probe that exits quickly after enqueuing work. Avoid long-running mount handlers that do heavy processing directly.

SQLite is appropriate for the ledger, especially with transactions and WAL. Backups must use SQLite's backup mechanisms or include WAL state; do not treat a copied `.sqlite` file as complete while WAL is active.

## Recommended MVP Shape

Build the system as a recoverable pipeline:

1. Detect and copy audio.
2. Create or update a ledger job.
3. Submit to `odin`.
4. Persist transcript and ASR metadata.
5. Classify by deterministic prefix.
6. Write Markdown staging notes.
7. Consolidate logs from staged state.
8. Expose read-only status and narrow write actions to OpenClaw.
