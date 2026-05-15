from __future__ import annotations

import os
import socket
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread

from logbook.ledger import open_ledger


MB = 1024 * 1024


def ensure_pipeline_telemetry_schema(sqlite_path: Path) -> None:
    ledger = open_ledger(sqlite_path, initialize=True)
    try:
        with ledger.connection:
            _refresh_pipeline_stage_durations(ledger.connection, updated_at=_now_iso(None))
    finally:
        ledger.close()


@dataclass
class SQLitePipelineReporter:
    sqlite_path: Path
    run_id: str
    _heartbeat_interval_seconds: float | None = None
    _stop_event: Event | None = field(default=None, init=False, repr=False)
    _heartbeat_thread: Thread | None = field(default=None, init=False, repr=False)

    @classmethod
    def start(
        cls,
        sqlite_path: Path,
        *,
        command: str,
        host: str | None = None,
        pid: int | None = None,
        heartbeat_interval_seconds: float | None = None,
        now=None,
    ) -> "SQLitePipelineReporter":
        ensure_pipeline_telemetry_schema(sqlite_path)
        started_at = _now_iso(now)
        run_id = f"run-{uuid.uuid4().hex}"
        ledger = open_ledger(sqlite_path, initialize=True)
        try:
            with ledger.connection:
                ledger.connection.execute(
                    """
                    INSERT INTO pipeline_runs (
                        id, command, host, pid, started_at, heartbeat_at, status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'running')
                    """,
                    (
                        run_id,
                        command,
                        host or socket.gethostname(),
                        pid if pid is not None else os.getpid(),
                        started_at,
                        started_at,
                    ),
                )
        finally:
            ledger.close()
        reporter = cls(
            sqlite_path=sqlite_path,
            run_id=run_id,
            _heartbeat_interval_seconds=heartbeat_interval_seconds,
        )
        reporter._start_background_heartbeat()
        return reporter

    def heartbeat(self, *, now=None) -> None:
        self._set_heartbeat(_now_iso(now))

    def start_stage(
        self,
        stage: str,
        *,
        job_id: int | None = None,
        input_bytes: int | None = None,
        audio_seconds: float | None = None,
        route_kind: str | None = None,
        model: str | None = None,
        progress_total: int | None = None,
        safe_detail: str | None = None,
        now=None,
    ) -> None:
        self._record_stage_event(
            stage=stage,
            event="started",
            job_id=job_id,
            input_bytes=input_bytes,
            audio_seconds=audio_seconds,
            route_kind=route_kind,
            model=model,
            progress_total=progress_total,
            progress_kind="unknown",
            safe_detail=safe_detail,
            now=now,
        )

    def finish_stage(
        self,
        stage: str,
        *,
        event: str = "succeeded",
        safe_detail: str | None = None,
        job_id: int | None = None,
        input_bytes: int | None = None,
        audio_seconds: float | None = None,
        route_kind: str | None = None,
        model: str | None = None,
        progress_current: int | None = None,
        progress_total: int | None = None,
        now=None,
    ) -> None:
        occurred_at = _now(now)
        start_context = self._stage_start_context(stage, job_id)
        duration_seconds = None
        if start_context is not None:
            started_at = _parse_iso(start_context["occurred_at"])
            if started_at is not None:
                duration_seconds = max(0.0, (occurred_at - started_at).total_seconds())
            input_bytes = input_bytes if input_bytes is not None else start_context["input_bytes"]
            audio_seconds = audio_seconds if audio_seconds is not None else start_context["audio_seconds"]
            route_kind = route_kind or start_context["route_kind"]
            model = model or start_context["model"]
        self._record_stage_event(
            stage=stage,
            event=event,
            job_id=job_id,
            input_bytes=input_bytes,
            audio_seconds=audio_seconds,
            route_kind=route_kind,
            model=model,
            progress_current=progress_current,
            progress_total=progress_total,
            progress_kind="unknown",
            duration_seconds=duration_seconds,
            safe_detail=safe_detail,
            now=lambda: occurred_at,
        )

    def advance_stage(
        self,
        stage: str,
        *,
        progress_current: int,
        progress_total: int,
        progress_kind: str = "measured",
        job_id: int | None = None,
        input_bytes: int | None = None,
        audio_seconds: float | None = None,
        route_kind: str | None = None,
        model: str | None = None,
        safe_detail: str | None = None,
        now=None,
    ) -> None:
        start_context = self._stage_start_context(stage, job_id)
        if start_context is not None:
            input_bytes = input_bytes if input_bytes is not None else start_context["input_bytes"]
            audio_seconds = audio_seconds if audio_seconds is not None else start_context["audio_seconds"]
            route_kind = route_kind or start_context["route_kind"]
            model = model or start_context["model"]
        self._record_stage_event(
            stage=stage,
            event="progress",
            job_id=job_id,
            input_bytes=input_bytes,
            audio_seconds=audio_seconds,
            route_kind=route_kind,
            model=model,
            progress_current=progress_current,
            progress_total=progress_total,
            progress_kind=progress_kind,
            safe_detail=safe_detail,
            now=now,
        )

    def finish_run(self, *, status: str, exit_code: int, now=None) -> None:
        self.close()
        finished_at = _now_iso(now)
        ledger = open_ledger(self.sqlite_path)
        try:
            with ledger.connection:
                ledger.connection.execute(
                    """
                    UPDATE pipeline_runs
                    SET status = ?,
                        exit_code = ?,
                        finished_at = ?,
                        heartbeat_at = ?
                    WHERE id = ?
                    """,
                    (status, exit_code, finished_at, finished_at, self.run_id),
                )
        finally:
            ledger.close()

    def _record_stage_event(
        self,
        *,
        stage: str,
        event: str,
        job_id: int | None = None,
        input_bytes: int | None = None,
        audio_seconds: float | None = None,
        route_kind: str | None = None,
        model: str | None = None,
        progress_current: int | None = None,
        progress_total: int | None = None,
        progress_kind: str = "unknown",
        duration_seconds: float | None = None,
        safe_detail: str | None = None,
        now=None,
    ) -> None:
        occurred_at = _now_iso(now)
        normalized_route_kind = route_kind or "unknown"
        normalized_model = model or "unknown"
        input_size_bucket = _input_size_bucket(input_bytes)
        progress_percent = None
        if progress_current is not None and progress_total:
            progress_percent = max(0.0, min(100.0, (progress_current / progress_total) * 100.0))
        ledger = open_ledger(self.sqlite_path)
        try:
            with ledger.connection:
                ledger.connection.execute(
                    """
                    INSERT INTO pipeline_stage_events (
                        run_id, job_id, stage, event, occurred_at, progress_current,
                        progress_total, progress_percent, progress_kind, input_bytes,
                        audio_seconds, route_kind, model, input_size_bucket,
                        duration_seconds, safe_detail
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.run_id,
                        job_id,
                        stage,
                        event,
                        occurred_at,
                        progress_current,
                        progress_total,
                        progress_percent,
                        progress_kind,
                        input_bytes,
                        audio_seconds,
                        normalized_route_kind,
                        normalized_model,
                        input_size_bucket,
                        duration_seconds,
                        safe_detail,
                    ),
                )
                ledger.connection.execute(
                    """
                    UPDATE pipeline_runs
                    SET heartbeat_at = ?
                    WHERE id = ?
                    """,
                    (occurred_at, self.run_id),
                )
                if event == "succeeded" and duration_seconds is not None:
                    _refresh_pipeline_stage_durations(ledger.connection, updated_at=occurred_at)
        finally:
            ledger.close()

    def _stage_start_context(self, stage: str, job_id: int | None) -> dict[str, object] | None:
        ledger = open_ledger(self.sqlite_path)
        try:
            row = ledger.connection.execute(
                """
                SELECT occurred_at, input_bytes, audio_seconds, route_kind, model
                FROM pipeline_stage_events
                WHERE run_id = ?
                  AND stage = ?
                  AND job_id IS ?
                  AND event = 'started'
                ORDER BY id DESC
                LIMIT 1
                """,
                (self.run_id, stage, job_id),
            ).fetchone()
        finally:
            ledger.close()
        if row is None:
            return None
        return {
            "occurred_at": row["occurred_at"],
            "input_bytes": row["input_bytes"],
            "audio_seconds": row["audio_seconds"],
            "route_kind": row["route_kind"] or "unknown",
            "model": row["model"] or "unknown",
        }

    def _set_heartbeat(self, heartbeat_at: str) -> None:
        ledger = open_ledger(self.sqlite_path)
        try:
            with ledger.connection:
                ledger.connection.execute(
                    """
                    UPDATE pipeline_runs
                    SET heartbeat_at = ?
                    WHERE id = ?
                    """,
                    (heartbeat_at, self.run_id),
                )
        finally:
            ledger.close()

    def close(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._heartbeat_thread is not None and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=2.0)

    def _start_background_heartbeat(self) -> None:
        interval = self._heartbeat_interval_seconds
        if interval is None or interval <= 0:
            return
        self._stop_event = Event()
        self._heartbeat_thread = Thread(
            target=self._heartbeat_loop,
            args=(self._stop_event, interval),
            name=f"logbook-pipeline-heartbeat-{self.run_id}",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _heartbeat_loop(self, stop_event: Event, interval: float) -> None:
        while not stop_event.wait(interval):
            try:
                self.heartbeat()
            except Exception:
                pass


def _now_iso(now) -> str:
    value = _now(now)
    return value.isoformat(timespec="seconds")


def _now(now) -> datetime:
    value = now() if now is not None else datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_iso(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _input_size_bucket(input_bytes: int | None) -> str:
    if input_bytes is None:
        return "unknown"
    size_mb = max(0.0, input_bytes / MB)
    if size_mb < 1:
        return "0-1mb"
    if size_mb < 10:
        return "1-10mb"
    if size_mb < 100:
        return "10-100mb"
    if size_mb < 500:
        return "100-500mb"
    return "500mb+"


def _refresh_pipeline_stage_durations(connection, *, updated_at: str) -> None:
    rows = connection.execute(
        """
        SELECT finish.stage,
               COALESCE(finish.route_kind, started.route_kind, 'unknown') AS route_kind,
               COALESCE(finish.model, started.model, 'unknown') AS model,
               COALESCE(
                   finish.input_size_bucket,
                   started.input_size_bucket,
                   'unknown'
               ) AS input_size_bucket,
               COALESCE(finish.input_bytes, started.input_bytes) AS input_bytes,
               COALESCE(
                   finish.duration_seconds,
                   (julianday(finish.occurred_at) - julianday(started.occurred_at)) * 86400.0
               ) AS duration_seconds
        FROM pipeline_stage_events AS finish
        JOIN pipeline_stage_events AS started
          ON started.id = (
              SELECT candidate.id
              FROM pipeline_stage_events AS candidate
              WHERE candidate.run_id = finish.run_id
                AND candidate.stage = finish.stage
                AND candidate.job_id IS finish.job_id
                AND candidate.event = 'started'
                AND candidate.id < finish.id
              ORDER BY candidate.id DESC
              LIMIT 1
          )
        WHERE finish.event = 'succeeded'
        """
    ).fetchall()
    grouped: dict[tuple[str, str, str, str], list[tuple[int, int | None]]] = {}
    for row in rows:
        duration_seconds = row["duration_seconds"]
        if duration_seconds is None:
            continue
        key = (
            row["stage"],
            row["route_kind"] or "unknown",
            row["model"] or "unknown",
            row["input_size_bucket"] or "unknown",
        )
        grouped.setdefault(key, []).append((int(round(duration_seconds)), row["input_bytes"]))

    connection.execute("DELETE FROM pipeline_stage_durations")
    for (stage, route_kind, model, input_size_bucket), samples in grouped.items():
        durations = [duration for duration, _ in samples]
        total_mb = sum((input_bytes or 0) / MB for _, input_bytes in samples)
        average_seconds_per_mb = sum(durations) / total_mb if total_mb > 0 else None
        connection.execute(
            """
            INSERT INTO pipeline_stage_durations (
                stage, route_kind, model, input_size_bucket, sample_count,
                duration_p50_seconds, duration_p90_seconds, average_seconds_per_mb,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stage,
                route_kind,
                model,
                input_size_bucket,
                len(durations),
                _percentile(durations, 0.50),
                _percentile(durations, 0.90),
                average_seconds_per_mb,
                updated_at,
            ),
        )


def _percentile(values: list[int], quantile: float) -> int:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * quantile + 0.999999) - 1))
    return ordered[index]
