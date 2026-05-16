# Pipeline Observer And Watch Program

Updated: 2026-05-16

## Intent

The observer should make Logbook feel operationally transparent while staying
outside the processing path. It must answer four operator questions quickly:

- Is anything running right now?
- What finished recently, and did it succeed?
- What failed or needs review?
- How long is the current or next job likely to take?

The May 2026 UI refactor adds two first-class observer presentations on top of
the same snapshot contract: a packaged web dashboard and an interactive curses
terminal dashboard. Both remain read-only and path-safe.

The observer is read-only. The pipeline may write progress telemetry to SQLite,
but the watch program must not mutate job state, delete files, invoke recovery
actions, or acquire the vault write lock.

## Requirement Review

The requested surface is directionally right: recent finished jobs, currently
running work, failures, successes, durations, basic statistics, and a compact
progress display with an ETA when true progress is unavailable.

Important watch-program details to add:

- Staleness: show when the last pipeline heartbeat was seen and warn if the
  current run appears abandoned.
- Confidence: label progress as `measured`, `estimated`, or `unknown` so an ETA
  is never mistaken for truth.
- Filters: let an operator focus by job ID, ledger status, stage, route kind,
  failures, dead letters, or a recent time window.
- Single snapshot endpoint: expose a consistent read-only API response so the
  watch program, OpenClaw, and scripts do not stitch together conflicting reads.
- Machine output: support `--json` and `--once` for automation, not only a live
  terminal view.
- Privacy: keep source audio paths, transcript paths, tokens, and transcript
  text out of the default view.
- Exit semantics: optionally exit nonzero when failures, stale runs, or backlog
  thresholds are present so the command can be used in scripts.
- Accessibility and terminal safety: compact color is useful, but status text
  and symbols must remain readable in plain, no-color, and narrow terminals.
- Bounded retention: progress and history data should be useful for estimates
  but not grow forever without a pruning policy.

## Current Architecture Assessment

Logbook already has the right durable core:

- SQLite is the source of truth for idempotency and job state.
- `process-mounted-recorder` runs the main pipeline stages in order: copy,
  transcribe, diarize meetings, route, consolidate, mark vault sync, and sync
  memory graph evidence.
- The FastAPI status API exposes health, jobs, logs, dead letters, cleanup, and
  memory graph health.
- Prometheus metrics expose aggregate health and counts.
- `odin` exposes health, job result, and aggregate worker metrics.

The current gap is granularity. Ledger statuses show durable milestones such as
`copied`, `transcribed`, `meeting_written`, and `consolidated`, but they do not
represent an active run heartbeat, per-stage progress, per-stage failures, or
stage-duration history. Some CLI commands print useful lines to stdout, but an
observer should not scrape mutable logs as its primary source.

## Recommended Approach

Build LGB-039 as a read-only observer surface backed by lightweight pipeline
telemetry in the same SQLite ledger. Keep existing job-state transitions as the
processing source of truth, and add a separate telemetry layer for observation.

## Approaches Considered

### Option A: Scrape launchd/stdout logs

This would be quick because `process-mounted-recorder` already prints stage
counts. It is not recommended. Logs are not transactional, are hard to correlate
with ledger rows, can miss the current stage during buffering, and can leak
details that should not become an API contract.

### Option B: Derive everything from existing ledger rows

This is safe and should be the first implementation step. It can report recent
finished jobs, existing statuses, dead letters, and some durations from current
timestamps. It cannot reliably show a live stage, heartbeat, stale run, or
in-stage progress.

### Option C: Add read-only telemetry beside the ledger

This is the recommended architecture. It adds a small progress-reporting seam to
the pipeline while keeping the observer read-only. It provides current-run
visibility, measured or estimated progress, stale-run detection, and duration
history without changing the existing recovery model.

### 1. Pipeline Telemetry Tables

Add append-friendly tables that describe what happened without deciding what
should happen next:

```text
pipeline_runs
  id
  command
  host
  pid
  started_at
  heartbeat_at
  finished_at
  status              running | succeeded | failed | abandoned
  exit_code
  version

pipeline_stage_events
  id
  run_id
  job_id              nullable for whole-run stages
  stage               copy | transcribe | diarize | route | consolidate | vault_sync | memory_sync | cleanup
  event               queued | started | progress | succeeded | failed | skipped
  occurred_at
  progress_current
  progress_total
  progress_percent
  progress_kind       measured | estimated | unknown
  input_bytes
  audio_seconds
  message_code
  safe_detail

pipeline_stage_durations
  stage
  route_kind
  model
  input_size_bucket
  sample_count
  duration_p50_seconds
  duration_p90_seconds
  average_seconds_per_mb
  updated_at
```

`pipeline_stage_durations` can be rebuilt from successful stage events, but a
small materialized table keeps the watch command fast and allows an exponential
moving average to update after every completed stage.

### 2. Progress Reporting Interface

Introduce a small progress reporter interface used by the pipeline:

```text
start_run(command)
heartbeat()
start_stage(stage, job_id, input_bytes, audio_seconds)
advance(stage, current, total, kind)
finish_stage(stage, outcome, safe_detail)
finish_run(outcome, exit_code)
```

Default reporter: no-op, useful for tests and isolated library calls.

Production reporter: SQLite-backed, used by `process-mounted-recorder` and any
future scheduled pipeline command.

This keeps implementation localized. Individual modules do not need to know
about the watch UI; they only receive a reporter and emit safe stage events.

### 3. Real Progress Where It Is Cheap

Use measured progress only when it is reliable:

- Copy: switch recorder copy to chunked reads so copied bytes provide true
  progress against `size_bytes`.
- Routing, consolidation, vault sync, memory sync, cleanup: count jobs, files,
  commands, or graph items.
- Transcription and diarization: use measured progress only if `odin` later
  exposes worker-side progress. Until then, treat them as estimated.

### 4. ETA From History When Progress Is Not Real

When a stage cannot report real progress, estimate from historical observations:

- Key history by stage, route kind, model, and input size bucket.
- Prefer p50 for ETA and show p90 as a risk hint in details.
- Compute estimated percent as `elapsed / predicted_duration`, capped below
  completion until the stage actually succeeds.
- If elapsed exceeds the predicted duration, show `over estimate` instead of
  pretending the stage is stuck at 100 percent.
- If too few samples exist, show `collecting baseline` and omit the ETA rather
  than inventing precision.

The watch UI should always display the progress kind beside the bar, for example
`estimated from 18 similar transcribe jobs`.

### 5. Observer Snapshot API

Add one read-only endpoint:

```text
GET /observer/snapshot
```

Response shape:

```yaml
observer_snapshot:
  generated_at: datetime
  health:
    api: ok
    sqlite: ok
    odin: ok | unavailable | unknown
    memgraph: ok | unavailable | not_configured | unknown
  current_run:
    run_id: string
    command: process-mounted-recorder
    status: running
    started_at: datetime
    heartbeat_at: datetime
    elapsed_seconds: integer
    stale: boolean
  active_stage:
    job_id: integer
    stage: transcribe
    status: running
    progress_percent: number
    progress_kind: measured | estimated | unknown
    eta_seconds: integer
    confidence: high | medium | low | none
  recent_finished:
    - job_id: integer
      status: vault_synced
      classification: log | category | meeting | dead_letter
      duration_seconds: integer
      finished_at: datetime
  recent_failures:
    - job_id: integer
      stage: vault_sync
      safe_detail: generated_path_missing_from_pushed_head
      occurred_at: datetime
  stats:
    window: 24h
    jobs_seen: integer
    succeeded: integer
    failed: integer
    dead_letters: integer
    p50_duration_seconds: integer
    p90_duration_seconds: integer
```

The snapshot should be assembled inside one SQLite read transaction. It should
reuse existing health, cleanup, memory graph, and metric helpers instead of
forking new logic.

### 6. Watch CLI

Add a compact operator command:

```bash
logbook watch --env .env
logbook watch --env .env --once
logbook watch --env .env --json
logbook watch --env .env --status failed --since 24h
logbook watch --api http://127.0.0.1:8788 --read-token-env LOGBOOK_READ_TOKEN
logbook watch --ui curses
```

Default terminal layout:

```text
Logbook 14:32:10  api ok  db ok  odin ok  graph ok  heartbeat 3s
Run process-mounted-recorder  elapsed 06:42  stage transcribe  job 91
[###############.....] 76% estimated  ETA 01:18  42 MB  18 samples

Recent finished
  ok  #89 meeting  vault_synced  08:44  12:18
  ok  #88 log      consolidated  02:11  12:09
  dl  #85 unknown  dead_letter   01:36  11:44

Failures and review
  none in 24h

Stats 24h  jobs 11  ok 9  dead_letters 2  failed 0  p50 02:04  p90 09:12
```

The display should avoid large boxes, verbose prose, and wide tables. It should
fall back to plain text when stdout is not a TTY.

The curses implementation uses the Python standard library, keeps a stable
layout during terminal resize, and preserves script-friendly `--once` rendering
for screenshots, tests, and incident notes.

### 7. Web Watch UI

Start the packaged web UI with:

```bash
logbook watch-web --env .env --host 127.0.0.1 --port 8790
```

The command starts a loopback FastAPI app that serves the built watcher assets
and the same `/observer/snapshot` contract. The frontend source lives in
`web/observer` and follows the current shadcn/ui Vite setup: React,
TypeScript, Tailwind CSS, `components.json`, local `components/ui/*` primitives,
and Lucide icons. The production bundle is emitted to
`src/logbook/static/watch` so the Python command can start the UI without a
separate Node process.

The web interface is compact rather than marketing-like: health chips, active
work, measured or estimated progress, ETA notes, rolling statistics, recent
successes, and failures are visible on the first screen. It automatically
switches day/night appearance from the browser's local clock and refreshes the
snapshot without requiring a page reload.

### 8. Metrics And Alerts

Keep Prometheus for fleet-level and alerting concerns. Add path-safe metrics
after the telemetry table exists:

- `logbook_pipeline_run_active`
- `logbook_pipeline_run_stale`
- `logbook_pipeline_stage_active{stage="..."}`
- `logbook_pipeline_stage_progress_percent{stage="...",kind="measured|estimated|unknown"}`
- `logbook_pipeline_eta_seconds{stage="..."}`
- `logbook_pipeline_stage_duration_seconds{stage="...",quantile="0.5|0.9"}`
- `logbook_pipeline_failures_recent`

The watch UI can read the snapshot API or SQLite directly; Prometheus remains
the alerting system, not the source for per-job truth.

## Architecture Decision

The observer should be a read-only consumer over a pipeline telemetry seam, not
a supervising daemon. This preserves locality:

- Processing correctness stays in the ledger and existing modules.
- Progress emission is concentrated in the pipeline command and a reporter.
- Display logic lives in the watch command and snapshot API.
- OpenClaw keeps the same bounded access model and can consume the snapshot
  without receiving shell authority.

## Implementation Phases

### Phase 1: Snapshot From Existing Ledger

Deliver a useful `logbook watch --once` and `GET /observer/snapshot` using only
existing ledger timestamps and statuses. This immediately reports recent
finished jobs, dead letters, failures visible in durable state, and basic stats.

Implementation note: Phase 1 landed on 2026-05-15 as a ledger-derived snapshot.
It reported `current_run` and `active_stage` as empty until Phase 2 added run
heartbeats and stage events.

### Phase 2: Run Heartbeats And Stage Events

Add `pipeline_runs`, `pipeline_stage_events`, and the SQLite reporter. Instrument
`process-mounted-recorder` around each top-level stage. The observer can now show
the active run, stale run detection, and stage-level elapsed time.

Implementation note: Phase 2 landed on 2026-05-15. The ledger now initializes
`pipeline_runs` and `pipeline_stage_events`; `process-mounted-recorder` records
copy, transcribe, diarize, route, consolidate, and vault-sync stage events; and
the observer snapshot reports the latest running command, heartbeat age, stale
status, active stage, stage elapsed time, and path-redacted safe details. A
background heartbeat keeps long stages from being misidentified as stale while
they are still active.

### Phase 3: ETA History

Materialize `pipeline_stage_durations` from completed stage events. Add estimated
progress for transcribe/diarize and any other stage without real progress.

Implementation note: Phase 3 landed on 2026-05-15. Successful stage completions
now update `pipeline_stage_durations` by stage, route kind, model, and input-size
bucket. The materializer can rebuild from legacy Phase 2 start/succeeded event
pairs, and active stages use p50 history for estimated percent and ETA once at
least three comparable samples exist. Sparse history remains labelled
`unknown` with `collecting baseline`; estimates include sample count, confidence,
p50 ETA, and p90 risk duration.

### Phase 4: Measured Progress

Add chunked copy progress and count-based progress for routing, consolidation,
vault sync, memory sync, and cleanup. Treat `odin` transcription/diarization as
estimated until the worker exposes real progress.

Implementation note: Phase 4 landed on 2026-05-15. The copy path now reports
measured byte progress while copying recorder files in chunks, and the SQLite
reporter exposes `progress` events through `advance_stage`. The mounted-recorder
pipeline emits measured progress for copy, route, consolidation, and vault-sync
stages; the observer prefers measured progress over ETA history and preserves
elapsed stage time from the original `started` event.

### Phase 5: Polished Watch UI

Add the live compact terminal refresh, filters, JSON output, no-color mode, exit
codes, and API-backed remote mode.

Implementation note: Phase 5 landed on 2026-05-15. `logbook watch` now supports
live in-place refresh, one-shot and JSON modes, local SQLite or remote
`--api` snapshots with bearer-token environment lookup, status filtering,
script-friendly failure/stale exit policies, automatic day/night appearance,
explicit `--theme day|night|auto`, `--no-color`, and `--ui compact|full`. The
compact renderer stays path-safe and readable for logs and automation. The full
terminal dashboard adds bounded panels for health, current run/stage progress,
recent finished jobs, failures/review, statistics, and live operator controls
for quit, refresh, failure/all filtering, and refresh interval changes. Local
CLI and API snapshots also run short read-only Odin and Memgraph reachability
probes so the health header reports `ok`, `unavailable`, or `not_configured`
instead of leaving configured services at `unknown`.

### Phase 6: Modern Web And Curses Surfaces

Add `logbook watch-web` and `logbook watch --ui curses` while keeping the
observer snapshot contract unchanged.

Implementation note: Phase 6 landed on 2026-05-16. The web watcher serves a
packaged React/Vite UI with shadcn-style local components and automatic
day/night appearance from local time. The curses watcher adds a full
terminal-only view with compact panels, progress bars, filters, and live key
controls. The quality gate now includes `mypy`, the web production build, and a
97% changed-line coverage threshold.

## Acceptance Criteria

- `logbook watch --once --env .env` reports recent finished jobs, current run
  state, failures, successes, durations, and 24-hour statistics without writing
  to SQLite or the vault.
- `logbook watch --env .env` refreshes in place, remains readable in an 80-column
  terminal, and falls back to plain text for non-TTY output.
- A currently running mount pipeline shows a stage, elapsed time, heartbeat age,
  and progress bar.
- Progress is labelled `measured`, `estimated`, or `unknown`; estimated progress
  includes ETA confidence and sample count.
- The observer warns when the active run heartbeat is stale.
- Default output does not expose source audio paths, transcript paths, bearer
  tokens, or transcript text.
- `GET /observer/snapshot` is read-only, token-protected like other status
  endpoints, and represented in OpenAPI.
- Prometheus exposes aggregate observer metrics without adding path-sensitive
  labels.
- Tests cover snapshot generation, stale heartbeat detection, ETA fallback,
  privacy redaction, no-TTY output, and JSON output.
