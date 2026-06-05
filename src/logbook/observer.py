from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from logbook.config import AppConfig
from logbook.memory_graph import Neo4jMemgraphClient
from logbook.odin import HttpOdinClient


FINAL_SUCCESS_STATUSES = {
    "copied",
    "transcribed",
    "diarized",
    "inbox_written",
    "consolidated",
    "category_written",
    "meeting_written",
}
FINAL_REVIEW_STATUSES = {"dead_letter_written", "dead_letter_discarded"}
FINAL_STATUSES = FINAL_SUCCESS_STATUSES | FINAL_REVIEW_STATUSES
MIN_ETA_SAMPLES = 3


@dataclass(frozen=True)
class ObserverHealth:
    api: str
    sqlite: str
    odin: str
    memgraph: str

    def to_dict(self) -> dict[str, str]:
        return {
            "api": self.api,
            "sqlite": self.sqlite,
            "odin": self.odin,
            "memgraph": self.memgraph,
        }


@dataclass(frozen=True)
class ObserverJobOutcome:
    job_id: int
    status: str
    classification: str | None
    recorded_at: str | None
    finished_at: str
    duration_seconds: int | None
    vault_synced: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "classification": self.classification,
            "recorded_at": self.recorded_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "vault_synced": self.vault_synced,
        }


@dataclass(frozen=True)
class ObserverFailure:
    job_id: int
    status: str
    classification: str | None
    occurred_at: str
    safe_detail: str
    source: str = "job"
    run_id: str | None = None
    command: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "classification": self.classification,
            "occurred_at": self.occurred_at,
            "safe_detail": self.safe_detail,
            "source": self.source,
            "run_id": self.run_id,
            "command": self.command,
        }


@dataclass(frozen=True)
class ObserverStats:
    window: str
    jobs_seen: int
    succeeded: int
    failed: int
    dead_letters: int
    p50_duration_seconds: int
    p90_duration_seconds: int

    def to_dict(self) -> dict[str, object]:
        return {
            "window": self.window,
            "jobs_seen": self.jobs_seen,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "dead_letters": self.dead_letters,
            "p50_duration_seconds": self.p50_duration_seconds,
            "p90_duration_seconds": self.p90_duration_seconds,
        }


@dataclass(frozen=True)
class ObserverSnapshot:
    generated_at: str
    latest_finished_at: str | None
    health: ObserverHealth
    current_run: dict[str, object] | None
    active_stage: dict[str, object] | None
    recent_finished: tuple[ObserverJobOutcome, ...]
    recent_failures: tuple[ObserverFailure, ...]
    stats: ObserverStats

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at,
            "latest_finished_at": self.latest_finished_at,
            "health": self.health.to_dict(),
            "current_run": self.current_run,
            "active_stage": self.active_stage,
            "recent_finished": [item.to_dict() for item in self.recent_finished],
            "recent_failures": [item.to_dict() for item in self.recent_failures],
            "stats": self.stats.to_dict(),
        }


def build_observer_snapshot(
    config: AppConfig,
    *,
    generated_at: datetime | None = None,
    window_hours: int = 24,
    limit: int = 5,
    stale_after_seconds: int = 300,
    probe_services: bool = False,
    service_timeout_seconds: float = 1.0,
) -> ObserverSnapshot:
    generated = _normalize_datetime(generated_at or datetime.now(timezone.utc))
    generated_iso = generated.isoformat(timespec="seconds")
    window_start = generated - timedelta(hours=window_hours)

    try:
        rows = _read_recording_jobs(config.sqlite_path)
    except sqlite3.Error:
        return ObserverSnapshot(
            generated_at=generated_iso,
            latest_finished_at=None,
            health=ObserverHealth(
                api="ok",
                sqlite="unavailable",
                odin=_odin_status(config, probe_services, service_timeout_seconds),
                memgraph=_memgraph_status(config, probe_services, service_timeout_seconds),
            ),
            current_run=None,
            active_stage=None,
            recent_finished=(),
            recent_failures=(),
            stats=ObserverStats(
                window=f"{window_hours}h",
                jobs_seen=0,
                succeeded=0,
                failed=0,
                dead_letters=0,
                p50_duration_seconds=0,
                p90_duration_seconds=0,
            ),
        )

    recent_rows = [
        row
        for row in rows
        if (seen_at := _parse_datetime(row.get("first_seen_at"))) is not None
        and seen_at >= window_start
    ]
    finished = sorted(
        (
            outcome
            for row in rows
            if (outcome := _finished_outcome(row, window_start)) is not None
        ),
        key=lambda item: (item.finished_at, item.job_id),
        reverse=True,
    )[:limit]
    job_failures = (
        failure
        for row in rows
        if (failure := _failure_outcome(row, window_start, config)) is not None
    )
    pipeline_failures = _failed_pipeline_runs(config.sqlite_path, window_start, config)
    failures = sorted(
        (
            failure
            for failure in (*job_failures, *pipeline_failures)
        ),
        key=lambda item: (item.occurred_at, item.job_id),
        reverse=True,
    )[:limit]
    durations = [
        duration
        for row in recent_rows
        if (duration := _duration_seconds(row)) is not None
    ]

    current_run = _current_run(config.sqlite_path, generated, stale_after_seconds)
    active_stage = (
        _active_stage(config.sqlite_path, current_run["run_id"], generated, config)
        if current_run is not None
        else None
    )

    return ObserverSnapshot(
        generated_at=generated_iso,
        latest_finished_at=_latest_finished_at(rows),
        health=ObserverHealth(
            api="ok",
            sqlite="ok",
            odin=_odin_status(config, probe_services, service_timeout_seconds),
            memgraph=_memgraph_status(config, probe_services, service_timeout_seconds),
        ),
        current_run=current_run,
        active_stage=active_stage,
        recent_finished=tuple(finished),
        recent_failures=tuple(failures),
        stats=ObserverStats(
            window=f"{window_hours}h",
            jobs_seen=len(recent_rows),
            succeeded=sum(1 for row in recent_rows if row.get("status") in FINAL_SUCCESS_STATUSES),
            failed=sum(1 for row in recent_rows if _is_failure_status(row.get("status")))
            + len(pipeline_failures),
            dead_letters=sum(1 for row in recent_rows if row.get("status") == "dead_letter_written"),
            p50_duration_seconds=_percentile(durations, 0.50),
            p90_duration_seconds=_percentile(durations, 0.90),
        ),
    )


def render_observer_snapshot(
    snapshot: ObserverSnapshot,
    *,
    theme: str = "auto",
    color: bool = False,
    now: datetime | None = None,
) -> str:
    resolved_theme = resolve_watch_theme(theme, now=now)
    run_line = "Run none"
    if snapshot.current_run is not None:
        run = snapshot.current_run
        stage = snapshot.active_stage or {}
        stale = " stale" if run.get("stale") else ""
        stage_part = f"  stage {stage.get('stage', '-')}"
        if stage.get("job_id") is not None:
            stage_part += f"  job {stage['job_id']}"
        run_line = (
            f"Run {run['command']}  elapsed {_format_duration(run['elapsed_seconds'])}"
            f"  heartbeat {run['heartbeat_age_seconds']}s{stale}{stage_part}"
        )
    lines = [
        (
            f"Logbook {format_observer_timestamp(snapshot.generated_at)}  view {resolved_theme}  "
            f"api {snapshot.health.api}  db {snapshot.health.sqlite}  odin {snapshot.health.odin}  "
            f"graph {snapshot.health.memgraph}"
        ),
        f"Latest finished job {format_observer_timestamp(snapshot.latest_finished_at)}",
        run_line,
    ]
    if snapshot.active_stage is not None:
        lines.append(_render_progress(snapshot.active_stage))
    lines.extend(["", "Recent finished"])
    if snapshot.recent_finished:
        lines.extend(f"  {_render_finished(item)}" for item in snapshot.recent_finished)
    else:
        lines.append("  none in 24h")

    lines.extend(["", "Failures and review"])
    if snapshot.recent_failures:
        lines.extend(f"  {_render_failure(item)}" for item in snapshot.recent_failures)
    else:
        lines.append("  none in 24h")

    stats = snapshot.stats
    lines.extend(
        [
            "",
            (
                f"Stats {stats.window}  jobs {stats.jobs_seen}  ok {stats.succeeded}  "
                f"dead_letters {stats.dead_letters}  failed {stats.failed}  "
                f"p50 {_format_duration(stats.p50_duration_seconds)}  "
                f"p90 {_format_duration(stats.p90_duration_seconds)}"
            ),
        ]
    )
    rendered = "\n".join(lines) + "\n"
    return _colorize(rendered, resolved_theme) if color else rendered


def resolve_watch_theme(theme: str, *, now: datetime | None = None) -> str:
    if theme in {"day", "night"}:
        return theme
    current = now or datetime.now()
    return "day" if 7 <= current.hour < 19 else "night"


def render_full_observer_dashboard(
    snapshot: ObserverSnapshot,
    *,
    theme: str = "auto",
    color: bool = False,
    now: datetime | None = None,
    width: int = 100,
    height: int = 30,
) -> str:
    resolved_theme = resolve_watch_theme(theme, now=now)
    width = max(72, min(width, 140))
    lines = [
        _box_top(width),
        _box_line(
            f"LOGBOOK WATCH  {format_observer_timestamp(snapshot.generated_at)}  view {resolved_theme}",
            width,
        ),
        _box_line(
            f"LATEST FINISHED JOB  {format_observer_timestamp(snapshot.latest_finished_at)}",
            width,
        ),
        _box_line(
            (
                f"health api {snapshot.health.api}  db {snapshot.health.sqlite}  "
                f"odin {snapshot.health.odin}  graph {snapshot.health.memgraph}"
            ),
            width,
        ),
        _box_sep(width),
    ]

    if snapshot.current_run is None:
        lines.append(_box_line("RUN  none", width))
    else:
        run = snapshot.current_run
        stale = "  STALE" if run.get("stale") else ""
        lines.append(
            _box_line(
                (
                    f"RUN  {run['command']}  elapsed {_format_duration(run['elapsed_seconds'])}  "
                    f"heartbeat {run['heartbeat_age_seconds']}s{stale}"
                ),
                width,
            )
        )
    if snapshot.active_stage is None:
        lines.append(_box_line("STAGE  none", width))
    else:
        stage = snapshot.active_stage
        job = f"  job {stage['job_id']}" if stage.get("job_id") is not None else ""
        lines.append(
            _box_line(
                (
                    f"STAGE  {stage['stage']}{job}  elapsed "
                    f"{_format_duration(stage['elapsed_seconds'])}"
                ),
                width,
            )
        )
        lines.append(_box_line(_render_progress(stage), width))

    stats = snapshot.stats
    lines.extend(
        [
            _box_sep(width),
            _box_line(
                (
                    f"STATS  {stats.window}  jobs {stats.jobs_seen}  ok {stats.succeeded}  "
                    f"failed {stats.failed}  dead_letters {stats.dead_letters}  "
                    f"p50 {_format_duration(stats.p50_duration_seconds)}  "
                    f"p90 {_format_duration(stats.p90_duration_seconds)}"
                ),
                width,
            ),
            _box_sep(width),
            _box_line("RECENT FINISHED", width),
        ]
    )
    if snapshot.recent_finished:
        lines.extend(_box_line(_render_finished(item), width) for item in snapshot.recent_finished[:5])
    else:
        lines.append(_box_line("none in window", width))

    lines.extend([_box_sep(width), _box_line("FAILURES AND REVIEW", width)])
    if snapshot.recent_failures:
        lines.extend(_box_line(_render_failure(item), width) for item in snapshot.recent_failures[:5])
    else:
        lines.append(_box_line("none in window", width))

    lines.extend(
        [
            _box_sep(width),
            _box_line("CONTROLS  q quit  r refresh  f failures  a all  +/- interval", width),
            _box_bottom(width),
        ]
    )
    if len(lines) > height:
        lines = lines[: max(0, height - 1)] + [_box_bottom(width)]
    elif len(lines) < height:
        filler_count = height - len(lines)
        lines = lines[:-1] + [_box_line("", width) for _ in range(filler_count)] + [lines[-1]]
    rendered = "\n".join(_truncate(line, width) for line in lines) + "\n"
    return _colorize(rendered, resolved_theme) if color else rendered


def filter_observer_snapshot(snapshot: ObserverSnapshot, status_filter: str) -> ObserverSnapshot:
    if status_filter == "all":
        return snapshot
    if status_filter == "failed":
        return replace(snapshot, recent_finished=())
    if status_filter == "success":
        return replace(
            snapshot,
            recent_finished=tuple(
                item
                for item in snapshot.recent_finished
                if item.status != "dead_letter_written" and item.classification != "dead_letter"
            ),
            recent_failures=(),
        )
    if status_filter == "dead_letter":
        return replace(
            snapshot,
            recent_finished=tuple(
                item
                for item in snapshot.recent_finished
                if item.status == "dead_letter_written" or item.classification == "dead_letter"
            ),
            recent_failures=(),
        )
    return snapshot


def observer_snapshot_from_dict(payload: dict[str, object]) -> ObserverSnapshot:
    health = payload.get("health") if isinstance(payload.get("health"), dict) else {}
    stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
    recent_finished = payload.get("recent_finished")
    recent_failures = payload.get("recent_failures")
    return ObserverSnapshot(
        generated_at=str(payload.get("generated_at") or ""),
        latest_finished_at=_optional_str(payload.get("latest_finished_at")),
        health=ObserverHealth(
            api=str(health.get("api") or "unknown"),
            sqlite=str(health.get("sqlite") or "unknown"),
            odin=str(health.get("odin") or "unknown"),
            memgraph=str(health.get("memgraph") or "unknown"),
        ),
        current_run=payload.get("current_run") if isinstance(payload.get("current_run"), dict) else None,
        active_stage=payload.get("active_stage") if isinstance(payload.get("active_stage"), dict) else None,
        recent_finished=tuple(
            ObserverJobOutcome(
                job_id=int(item.get("job_id") or 0),
                status=str(item.get("status") or "unknown"),
                classification=_optional_str(item.get("classification")),
                recorded_at=_optional_str(item.get("recorded_at")),
                finished_at=str(item.get("finished_at") or ""),
                duration_seconds=(
                    int(item["duration_seconds"]) if item.get("duration_seconds") is not None else None
                ),
                vault_synced=bool(item.get("vault_synced")),
            )
            for item in recent_finished
            if isinstance(item, dict)
        )
        if isinstance(recent_finished, list)
        else (),
        recent_failures=tuple(
            ObserverFailure(
                job_id=int(item.get("job_id") or 0),
                status=str(item.get("status") or "unknown"),
                classification=_optional_str(item.get("classification")),
                occurred_at=str(item.get("occurred_at") or ""),
                safe_detail=str(item.get("safe_detail") or ""),
                source=str(item.get("source") or "job"),
                run_id=_optional_str(item.get("run_id")),
                command=_optional_str(item.get("command")),
            )
            for item in recent_failures
            if isinstance(item, dict)
        )
        if isinstance(recent_failures, list)
        else (),
        stats=ObserverStats(
            window=str(stats.get("window") or "24h"),
            jobs_seen=int(stats.get("jobs_seen") or 0),
            succeeded=int(stats.get("succeeded") or 0),
            failed=int(stats.get("failed") or 0),
            dead_letters=int(stats.get("dead_letters") or 0),
            p50_duration_seconds=int(stats.get("p50_duration_seconds") or 0),
            p90_duration_seconds=int(stats.get("p90_duration_seconds") or 0),
        ),
    )


def _read_recording_jobs(sqlite_path: Path) -> list[dict[str, object]]:
    uri = f"file:{sqlite_path}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT id, status, classification, parsed_recorded_at, first_seen_at,
                   last_seen_at, copied_at, submitted_to_odin_at, transcribed_at,
                   routed_at, consolidated_at, diarized_at, vault_synced_at,
                   cleanup_last_attempt_at, cleanup_last_error
            FROM recording_jobs
            ORDER BY id
            """
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def _current_run(
    sqlite_path: Path,
    generated_at: datetime,
    stale_after_seconds: int,
) -> dict[str, object] | None:
    uri = f"file:{sqlite_path}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                """
                SELECT id, command, host, pid, started_at, heartbeat_at, status
                FROM pipeline_runs
                WHERE status = 'running'
                ORDER BY started_at DESC
                LIMIT 1
                """
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    if row is None:
        return None

    started_at = _parse_datetime(row["started_at"])
    heartbeat_at = _parse_datetime(row["heartbeat_at"])
    elapsed = _elapsed_seconds(started_at, generated_at)
    heartbeat_age = _elapsed_seconds(heartbeat_at, generated_at)
    return {
        "run_id": row["id"],
        "command": row["command"],
        "host": row["host"],
        "pid": row["pid"],
        "status": row["status"],
        "started_at": _iso_or_none(started_at),
        "heartbeat_at": _iso_or_none(heartbeat_at),
        "elapsed_seconds": elapsed,
        "heartbeat_age_seconds": heartbeat_age,
        "stale": heartbeat_age > stale_after_seconds,
    }


def _active_stage(
    sqlite_path: Path,
    run_id: str,
    generated_at: datetime,
    config: AppConfig,
) -> dict[str, object] | None:
    uri = f"file:{sqlite_path}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                """
                SELECT latest.id, latest.job_id, latest.stage, latest.event,
                       latest.occurred_at, latest.progress_current,
                       latest.progress_total, latest.progress_percent,
                       latest.progress_kind, latest.input_bytes, latest.route_kind,
                       latest.model, latest.input_size_bucket, latest.safe_detail,
                       started.occurred_at AS stage_started_at
                FROM pipeline_stage_events
                AS latest
                LEFT JOIN pipeline_stage_events AS started
                  ON started.id = (
                      SELECT candidate.id
                      FROM pipeline_stage_events AS candidate
                      WHERE candidate.run_id = latest.run_id
                        AND candidate.stage = latest.stage
                        AND candidate.job_id IS latest.job_id
                        AND candidate.event = 'started'
                        AND candidate.id <= latest.id
                      ORDER BY candidate.id DESC
                      LIMIT 1
                  )
                WHERE latest.run_id = ?
                  AND latest.event IN ('started', 'progress')
                ORDER BY latest.id DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    if row is None or row["event"] not in {"started", "progress"}:
        return None

    started_at = _parse_datetime(row["stage_started_at"]) or _parse_datetime(row["occurred_at"])
    stage = {
        "stage": row["stage"],
        "status": "running",
        "event": row["event"],
        "job_id": row["job_id"],
        "started_at": _iso_or_none(started_at),
        "elapsed_seconds": _elapsed_seconds(started_at, generated_at),
        "progress_current": row["progress_current"],
        "progress_total": row["progress_total"],
        "progress_percent": row["progress_percent"],
        "progress_kind": row["progress_kind"],
        "input_bytes": row["input_bytes"],
        "route_kind": row["route_kind"] or "unknown",
        "model": row["model"] or "unknown",
        "input_size_bucket": row["input_size_bucket"] or _input_size_bucket(row["input_bytes"]),
        "safe_detail": _safe_detail(row["safe_detail"] or "", config) if row["safe_detail"] else None,
    }
    _apply_duration_estimate(sqlite_path, stage)
    return stage


def _failed_pipeline_runs(
    sqlite_path: Path,
    window_start: datetime,
    config: AppConfig,
) -> tuple[ObserverFailure, ...]:
    uri = f"file:{sqlite_path}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT runs.id, runs.command, runs.finished_at, runs.exit_code,
                       failed_stage.job_id, failed_stage.stage, failed_stage.safe_detail
                FROM pipeline_runs AS runs
                LEFT JOIN pipeline_stage_events AS failed_stage
                  ON failed_stage.id = (
                      SELECT candidate.id
                      FROM pipeline_stage_events AS candidate
                      WHERE candidate.run_id = runs.id
                        AND candidate.event = 'failed'
                      ORDER BY candidate.id DESC
                      LIMIT 1
                  )
                WHERE runs.status = 'failed'
                ORDER BY runs.finished_at DESC
                """
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return ()

    failures: list[ObserverFailure] = []
    for row in rows:
        occurred_at = _parse_datetime(row["finished_at"])
        if occurred_at is None or occurred_at < window_start:
            continue
        stage = _optional_str(row["stage"])
        detail = _optional_str(row["safe_detail"])
        fallback = f"exit_code={row['exit_code']}" if row["exit_code"] is not None else "failed"
        safe_detail = f"{stage}: {detail or fallback}" if stage else (detail or fallback)
        failures.append(
            ObserverFailure(
                job_id=int(row["job_id"]) if row["job_id"] is not None else 0,
                status="failed",
                classification="pipeline",
                occurred_at=occurred_at.isoformat(timespec="seconds"),
                safe_detail=_safe_detail(safe_detail, config),
                source="pipeline_run",
                run_id=str(row["id"]),
                command=str(row["command"] or "pipeline"),
            )
        )
    return tuple(failures)


def _apply_duration_estimate(sqlite_path: Path, stage: dict[str, object]) -> None:
    history = _duration_history(
        sqlite_path,
        stage=str(stage["stage"]),
        route_kind=str(stage.get("route_kind") or "unknown"),
        model=str(stage.get("model") or "unknown"),
        input_size_bucket=str(stage.get("input_size_bucket") or "unknown"),
    )
    sample_count = int(history["sample_count"]) if history is not None else 0
    stage["sample_count"] = sample_count
    stage["eta_seconds"] = None
    stage["confidence"] = "none"
    stage["eta_status"] = "collecting_baseline"
    stage["estimated_duration_seconds"] = None
    stage["p90_duration_seconds"] = None

    if stage.get("progress_percent") is not None and stage.get("progress_kind") != "unknown":
        stage["eta_status"] = "measured"
        return
    if history is None or sample_count < MIN_ETA_SAMPLES:
        return

    predicted = int(history["duration_p50_seconds"])
    p90 = int(history["duration_p90_seconds"])
    elapsed = int(stage["elapsed_seconds"])
    if predicted <= 0:
        return

    stage["progress_kind"] = "estimated"
    stage["estimated_duration_seconds"] = predicted
    stage["p90_duration_seconds"] = p90
    stage["confidence"] = _eta_confidence(sample_count)
    if elapsed >= predicted:
        stage["progress_percent"] = 99.0
        stage["eta_seconds"] = 0
        stage["eta_status"] = "over_estimate"
        return

    stage["progress_percent"] = round((elapsed / predicted) * 100.0, 1)
    stage["eta_seconds"] = predicted - elapsed
    stage["eta_status"] = "estimated"


def _duration_history(
    sqlite_path: Path,
    *,
    stage: str,
    route_kind: str,
    model: str,
    input_size_bucket: str,
) -> dict[str, object] | None:
    uri = f"file:{sqlite_path}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                """
                SELECT sample_count, duration_p50_seconds, duration_p90_seconds,
                       average_seconds_per_mb
                FROM pipeline_stage_durations
                WHERE stage = ?
                  AND route_kind = ?
                  AND model = ?
                  AND input_size_bucket = ?
                """,
                (stage, route_kind, model, input_size_bucket),
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    return dict(row) if row is not None else None


def _finished_outcome(
    row: dict[str, object],
    window_start: datetime,
) -> ObserverJobOutcome | None:
    status = str(row.get("status") or "")
    if status not in FINAL_STATUSES:
        return None
    finished_at = _finish_datetime(row)
    if finished_at is None or finished_at < window_start:
        return None
    return ObserverJobOutcome(
        job_id=int(row["id"]),
        status="vault_synced" if row.get("vault_synced_at") else status,
        classification=_optional_str(row.get("classification")),
        recorded_at=_optional_str(row.get("parsed_recorded_at")),
        finished_at=finished_at.isoformat(timespec="seconds"),
        duration_seconds=_duration_seconds(row),
        vault_synced=bool(row.get("vault_synced_at")),
    )


def _failure_outcome(
    row: dict[str, object],
    window_start: datetime,
    config: AppConfig,
) -> ObserverFailure | None:
    status = str(row.get("status") or "")
    error = _optional_str(row.get("cleanup_last_error"))
    if not _is_failure_status(status) and not error:
        return None
    occurred_at = (
        _parse_datetime(row.get("cleanup_last_attempt_at"))
        or _parse_datetime(row.get("last_seen_at"))
    )
    if occurred_at is None or occurred_at < window_start:
        return None
    return ObserverFailure(
        job_id=int(row["id"]),
        status=status,
        classification=_optional_str(row.get("classification")),
        occurred_at=occurred_at.isoformat(timespec="seconds"),
        safe_detail=_safe_detail(error or status, config),
    )


def _latest_finished_at(rows: list[dict[str, object]]) -> str | None:
    finished = [
        occurred_at
        for row in rows
        if (occurred_at := _job_finished_datetime(row)) is not None
    ]
    if not finished:
        return None
    return max(finished).isoformat(timespec="seconds")


def _job_finished_datetime(row: dict[str, object]) -> datetime | None:
    status = str(row.get("status") or "")
    if status in FINAL_STATUSES:
        return _finish_datetime(row)
    if _is_failure_status(status) or row.get("cleanup_last_error"):
        return _parse_datetime(row.get("cleanup_last_attempt_at")) or _parse_datetime(
            row.get("last_seen_at")
        )
    return None


def _finish_datetime(row: dict[str, object]) -> datetime | None:
    for key in (
        "vault_synced_at",
        "consolidated_at",
        "routed_at",
        "diarized_at",
        "transcribed_at",
        "copied_at",
        "last_seen_at",
    ):
        if parsed := _parse_datetime(row.get(key)):
            return parsed
    return None


def _duration_seconds(row: dict[str, object]) -> int | None:
    started = _parse_datetime(row.get("first_seen_at"))
    finished = _finish_datetime(row)
    if started is None or finished is None:
        return None
    return max(0, int((finished - started).total_seconds()))


def _is_failure_status(status: object) -> bool:
    value = str(status or "")
    return value.startswith("failed") or value.endswith("_failed") or "_failed_" in value


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return _normalize_datetime(datetime.fromisoformat(str(value)))
    except ValueError:
        return None


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _elapsed_seconds(started_at: datetime | None, generated_at: datetime) -> int:
    if started_at is None:
        return 0
    return max(0, int((generated_at - started_at).total_seconds()))


def _iso_or_none(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="seconds")


def _percentile(values: list[int], quantile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return ordered[index]


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    rendered = str(value)
    return rendered or None


def format_observer_timestamp(
    value: object,
    *,
    style: str = "iso",
    empty: str = "none",
) -> str:
    if not value:
        return empty
    parsed = _parse_datetime(value)
    if parsed is None:
        if style == "short":
            return str(value)[:16].replace("T", " ")
        return str(value)
    local = parsed.astimezone()
    if style == "short":
        return local.strftime("%Y-%m-%d %H:%M")
    return local.isoformat(timespec="seconds")


def _safe_detail(value: str, config: AppConfig) -> str:
    safe = value.replace(str(config.processing_root), "<processing_root>")
    safe = safe.replace(str(config.recorder.mount_path), "<recorder_mount>")
    return safe.replace("\n", " ")[:160]


def _input_size_bucket(input_bytes: object) -> str:
    if input_bytes is None:
        return "unknown"
    size_mb = max(0.0, int(input_bytes) / (1024 * 1024))
    if size_mb < 1:
        return "0-1mb"
    if size_mb < 10:
        return "1-10mb"
    if size_mb < 100:
        return "10-100mb"
    if size_mb < 500:
        return "100-500mb"
    return "500mb+"


def _eta_confidence(sample_count: int) -> str:
    if sample_count >= 10:
        return "high"
    if sample_count >= MIN_ETA_SAMPLES:
        return "medium"
    return "none"


def _odin_status(
    config: AppConfig,
    probe_services: bool,
    service_timeout_seconds: float,
) -> str:
    if not probe_services:
        return "unknown"
    try:
        health = HttpOdinClient(
            config.odin,
            timeout_seconds=service_timeout_seconds,
        ).health()
    except Exception:
        return "unavailable"
    return "ok" if health.healthy else "unavailable"


def _memgraph_status(
    config: AppConfig,
    probe_services: bool,
    service_timeout_seconds: float,
) -> str:
    if config.memgraph is None:
        return "not_configured"
    if not probe_services:
        return "unknown"
    try:
        client = Neo4jMemgraphClient(
            config.memgraph,
            connection_timeout_seconds=service_timeout_seconds,
        )
        try:
            client.query("RETURN 1 AS ok")
        finally:
            client.close()
    except Exception:
        return "unavailable"
    return "ok"


def _render_finished(item: ObserverJobOutcome) -> str:
    label = "dl" if item.classification == "dead_letter" else "ok"
    duration = _format_duration(item.duration_seconds)
    classification = item.classification or "unknown"
    finished_at = format_observer_timestamp(item.finished_at)
    return f"{label}  #{item.job_id} {classification}  {item.status}  {duration}  {finished_at}"


def _render_failure(item: ObserverFailure) -> str:
    if item.source == "pipeline_run":
        command = item.command or "pipeline"
        short_id = item.run_id.removeprefix("run-")[:8] if item.run_id else ""
        run_id = f" {short_id}" if short_id else ""
        occurred_at = format_observer_timestamp(item.occurred_at)
        return f"fail  run{run_id} {command}  {item.safe_detail}  {occurred_at}"
    classification = item.classification or "unknown"
    occurred_at = format_observer_timestamp(item.occurred_at)
    return f"fail  #{item.job_id} {classification}  {item.safe_detail}  {occurred_at}"


def _render_progress(stage: dict[str, object]) -> str:
    percent = stage.get("progress_percent")
    kind = stage.get("progress_kind") or "unknown"
    sample_count = int(stage.get("sample_count") or 0)
    if percent is None:
        status = "collecting baseline" if stage.get("eta_status") == "collecting_baseline" else "unknown"
        return f"Progress {kind}  {status}  {sample_count} samples"

    eta = stage.get("eta_seconds")
    parts = [f"{_progress_bar(float(percent))} {int(percent)}% {kind}"]
    if stage.get("eta_status") == "over_estimate":
        parts.append("over estimate")
    elif eta is not None:
        parts.append(f"ETA {_format_duration(int(eta))}")
    if sample_count:
        parts.append(f"{sample_count} samples")
    return "  ".join(parts)


def _progress_bar(percent: float) -> str:
    width = 20
    filled = max(0, min(width, int(round((percent / 100.0) * width))))
    return f"[{'#' * filled}{'.' * (width - filled)}]"


def _box_top(width: int) -> str:
    return "┌" + "─" * (width - 2) + "┐"


def _box_bottom(width: int) -> str:
    return "└" + "─" * (width - 2) + "┘"


def _box_sep(width: int) -> str:
    return "├" + "─" * (width - 2) + "┤"


def _box_line(text: str, width: int) -> str:
    return f"│ {_truncate(text, width - 4).ljust(width - 4)} │"


def _truncate(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "."


def _colorize(rendered: str, theme: str) -> str:
    if theme == "day":
        accent = "\033[34m"
        ok = "\033[32m"
        warn = "\033[31m"
    else:
        accent = "\033[96m"
        ok = "\033[92m"
        warn = "\033[91m"
    reset = "\033[0m"
    lines = []
    for line in rendered.splitlines():
        if line.startswith("Logbook ") or line.startswith("Run "):
            lines.append(f"{accent}{line}{reset}")
        elif line.strip().startswith("ok "):
            lines.append(f"{ok}{line}{reset}")
        elif line.strip().startswith(("fail ", "dl ")):
            lines.append(f"{warn}{line}{reset}")
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def _format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "--:--"
    minutes, remaining = divmod(max(0, seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{remaining:02d}"
    return f"{minutes:02d}:{remaining:02d}"
