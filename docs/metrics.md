# Logbook Metrics

LGB-031 adds Prometheus-style text metrics for the recorder/status API on `mimir`
and the internal `odin` worker. Metrics are path-safe: they expose counts, status
labels, and model metadata, not source audio paths, transcript paths, bearer
tokens, or vault filesystem paths.

## Scrape Targets

Recommended internal scrape targets from Prometheus on `odin`:

```yaml
scrape_configs:
  - job_name: logbook-api
    scrape_timeout: 30s
    static_configs:
      - targets:
          - 192.168.1.8:8788

  - job_name: logbook-odin-worker
    scrape_timeout: 15s
    static_configs:
      - targets:
          - 192.168.1.3:8765
```

Keep both endpoints internal. Do not route the Mac Mini to `odin` transcription
path through `fenrir`; `fenrir` remains only for explicitly approved
human-facing dashboards.

The Logbook API scrape includes a live graph-health check against Memgraph, so
use an explicit scrape timeout instead of relying on Prometheus defaults.
On `mimir`, port `8787` is reserved for the `clawdbot` CashClaw/OpenClaw
adapter; Logbook listens on `8788`.

## Recorder/API Metrics

The Logbook API exposes `/metrics` with:

- `logbook_up`
- `logbook_sqlite_reachable`
- `logbook_jobs_total`
- `logbook_jobs_by_status{status="..."}`
- `logbook_dead_letters`
- `logbook_open_log_entries`
- `logbook_latest_consolidation_age_seconds`
- `logbook_cleanup_eligible`
- `logbook_cleanup_blocked`
- `logbook_cleanup_local_pending`
- `logbook_cleanup_recorder_pending`
- `logbook_memory_graph_health_status{status="ok|drift|unavailable|not_configured"}`
- `logbook_memory_graph_drift`

## Odin Worker Metrics

The `odin` worker exposes `/metrics` with:

- `odin_worker_up`
- `odin_worker_model_ready`
- `odin_worker_jobs_in_memory`
- `odin_worker_jobs_by_status{status="..."}`
- `odin_worker_model_info{asr_model="...",device="...",compute_type="..."}`

## Alert Candidates

- API down: `up{job="logbook-api"} == 0` or `logbook_up == 0`
- SQLite unavailable: `logbook_sqlite_reachable == 0`
- Dead letters pending: `logbook_dead_letters > 0`
- Stale consolidation: `logbook_open_log_entries > 0` and
  `logbook_latest_consolidation_age_seconds > 86400`
- Cleanup waiting: `logbook_cleanup_local_pending > 0` or
  `logbook_cleanup_recorder_pending > 0`
- Graph drift: `logbook_memory_graph_drift == 1`
- Odin worker down: `up{job="logbook-odin-worker"} == 0` or
  `odin_worker_up == 0`
- Odin model unavailable: `odin_worker_model_ready == 0`
- Failed transcription: `odin_worker_jobs_by_status{status!="succeeded"} > 0`

Operator actions should remain bounded: inspect status, run dry-run repair or
cleanup commands first, and only execute destructive cleanup through the
retention gates.
