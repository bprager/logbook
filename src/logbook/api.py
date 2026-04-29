import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from logbook.config import AppConfig
from logbook.ledger import RecordingJob, open_ledger


API_TITLE = "Logbook Status API"
API_VERSION = "0.1.0"


class HealthResponse(BaseModel):
    status: str = Field(description="Overall read-only API health.")
    sqlite_reachable: bool
    job_count: int
    counts_by_status: dict[str, int]


class JobSummary(BaseModel):
    id: int
    status: str
    classification: Optional[str]
    recorded_at: Optional[str]
    source_filename: str
    obsidian_path: Optional[str]
    daily_log_path: Optional[str]
    first_seen_at: str
    last_seen_at: str


class JobDetail(JobSummary):
    checksum_prefix: str
    copied_at: Optional[str]
    submitted_to_odin_at: Optional[str]
    transcribed_at: Optional[str]
    routed_at: Optional[str]
    consolidated_at: Optional[str]
    late_arrival_at: Optional[str]
    asr_model: Optional[str]


class JobListResponse(BaseModel):
    count: int
    limit: int
    offset: int
    items: list[JobSummary]


class LogInboxItem(BaseModel):
    job_id: int
    recorded_at: str
    obsidian_path: str
    routed_at: Optional[str]


class LogInboxResponse(BaseModel):
    count: int
    items: list[LogInboxItem]


class OpenLogDateResponse(BaseModel):
    date: Optional[str]
    entry_count: int
    job_ids: list[int]


class ConsolidatedLogResponse(BaseModel):
    date: Optional[str]
    daily_log_path: str
    entry_count: int
    consolidated_at: Optional[str]
    job_ids: list[int]


class DeadLetterItem(BaseModel):
    job_id: int
    recorded_at: Optional[str]
    obsidian_path: Optional[str]
    review_status: str
    delete_after: Optional[str]
    routed_at: Optional[str]


class DeadLetterResponse(BaseModel):
    count: int
    items: list[DeadLetterItem]


@dataclass(frozen=True)
class LatestLogGroup:
    daily_log_path: str
    consolidated_at: Optional[str]
    jobs: tuple[RecordingJob, ...]


def create_app(config: AppConfig) -> FastAPI:
    bearer = HTTPBearer(auto_error=False)

    def require_read_token(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    ) -> None:
        expected = config.api.read_token if config.api is not None else None
        if expected is None:
            return
        if credentials is None or credentials.credentials != expected:
            raise HTTPException(status_code=401, detail="missing or invalid read token")

    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        summary="Read-only local status API for the Sony recorder to Obsidian Logbook pipeline.",
        description=(
            "This API exposes queue, routing, dead-letter, and consolidation status from the "
            "local SQLite ledger. It intentionally does not expose source audio paths and does "
            "not mutate processing state."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        swagger_ui_parameters={
            "defaultModelsExpandDepth": 1,
            "displayRequestDuration": True,
            "docExpansion": "list",
            "filter": True,
            "tryItOutEnabled": True,
        },
        contact={"name": "Logbook local operator"},
        license_info={"name": "CC0-1.0"},
        openapi_tags=[
            {"name": "system", "description": "Health and API metadata."},
            {"name": "jobs", "description": "Read-only recording job status."},
            {"name": "logs", "description": "Log inbox and consolidated daily log status."},
            {"name": "dead letters", "description": "Unknown-prefix transcripts awaiting review."},
        ],
    )

    read_dependencies = [Depends(require_read_token)]

    @app.get("/health", tags=["system"], response_model=HealthResponse)
    def health() -> HealthResponse:
        try:
            jobs = _all_jobs(config.sqlite_path)
        except sqlite3.Error:
            return HealthResponse(
                status="degraded",
                sqlite_reachable=False,
                job_count=0,
                counts_by_status={},
            )
        counts = Counter(job.status for job in jobs)
        return HealthResponse(
            status="ok",
            sqlite_reachable=True,
            job_count=len(jobs),
            counts_by_status=dict(sorted(counts.items())),
        )

    @app.get(
        "/jobs",
        tags=["jobs"],
        response_model=JobListResponse,
        dependencies=read_dependencies,
    )
    def jobs(
        status: Annotated[
            Optional[str],
            Query(description="Optional exact ledger status filter."),
        ] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> JobListResponse:
        loaded = _jobs(config.sqlite_path, status=status, limit=limit, offset=offset)
        count = _job_count(config.sqlite_path, status=status)
        return JobListResponse(
            count=count,
            limit=limit,
            offset=offset,
            items=[_job_summary(job) for job in loaded],
        )

    @app.get(
        "/jobs/{job_id}",
        tags=["jobs"],
        response_model=JobDetail,
        dependencies=read_dependencies,
    )
    def job_detail(job_id: int) -> JobDetail:
        job = _job_by_id(config.sqlite_path, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return _job_detail(job)

    @app.get(
        "/logs/inbox",
        tags=["logs"],
        response_model=LogInboxResponse,
        dependencies=read_dependencies,
    )
    def log_inbox() -> LogInboxResponse:
        loaded = _jobs_by_status(config.sqlite_path, "inbox_written", classification="log")
        items = [
            LogInboxItem(
                job_id=job.id,
                recorded_at=job.parsed_recorded_at or "",
                obsidian_path=job.obsidian_path or "",
                routed_at=job.routed_at,
            )
            for job in loaded
        ]
        return LogInboxResponse(count=len(items), items=items)

    @app.get(
        "/logs/open-date",
        tags=["logs"],
        response_model=OpenLogDateResponse,
        dependencies=read_dependencies,
    )
    def open_log_date() -> OpenLogDateResponse:
        loaded = _jobs_by_status(config.sqlite_path, "inbox_written", classification="log")
        if not loaded:
            return OpenLogDateResponse(date=None, entry_count=0, job_ids=[])
        open_date = (loaded[0].parsed_recorded_at or "")[:10] or None
        jobs_for_date = [
            job
            for job in loaded
            if job.parsed_recorded_at is not None and job.parsed_recorded_at[:10] == open_date
        ]
        return OpenLogDateResponse(
            date=open_date,
            entry_count=len(jobs_for_date),
            job_ids=[job.id for job in jobs_for_date],
        )

    @app.get(
        "/logs/consolidated/latest",
        tags=["logs"],
        response_model=ConsolidatedLogResponse,
        dependencies=read_dependencies,
    )
    def latest_consolidated_log() -> ConsolidatedLogResponse:
        latest = _latest_consolidated_log(config.sqlite_path)
        if latest is None:
            raise HTTPException(status_code=404, detail="no consolidated daily log found")
        first_recorded_at = latest.jobs[0].parsed_recorded_at if latest.jobs else None
        return ConsolidatedLogResponse(
            date=first_recorded_at[:10] if first_recorded_at else None,
            daily_log_path=latest.daily_log_path,
            entry_count=len(latest.jobs),
            consolidated_at=latest.consolidated_at,
            job_ids=[job.id for job in latest.jobs],
        )

    @app.get(
        "/dead-letters",
        tags=["dead letters"],
        response_model=DeadLetterResponse,
        dependencies=read_dependencies,
    )
    def dead_letters() -> DeadLetterResponse:
        loaded = _jobs_by_status(
            config.sqlite_path,
            "dead_letter_written",
            classification="dead_letter",
        )
        items = [
            DeadLetterItem(
                job_id=job.id,
                recorded_at=job.parsed_recorded_at,
                obsidian_path=job.obsidian_path,
                review_status="needs_review",
                delete_after=_delete_after(job.parsed_recorded_at),
                routed_at=job.routed_at,
            )
            for job in loaded
        ]
        return DeadLetterResponse(count=len(items), items=items)

    return app


def _all_jobs(sqlite_path: Path) -> list[RecordingJob]:
    return _jobs(sqlite_path, status=None, limit=10_000, offset=0)


def _jobs(
    sqlite_path: Path,
    status: Optional[str],
    limit: int,
    offset: int,
) -> list[RecordingJob]:
    ledger = open_ledger(sqlite_path)
    try:
        where = ""
        params: tuple[object, ...] = ()
        if status is not None:
            where = "WHERE status = ?"
            params = (status,)
        rows = ledger.connection.execute(
            f"""
            SELECT checksum_sha256
            FROM recording_jobs
            {where}
            ORDER BY id
            LIMIT ? OFFSET ?
            """,
            params + (limit, offset),
        ).fetchall()
        return [
            job
            for row in rows
            if (job := ledger.get_by_checksum(row["checksum_sha256"])) is not None
        ]
    finally:
        ledger.close()


def _job_count(sqlite_path: Path, status: Optional[str]) -> int:
    ledger = open_ledger(sqlite_path)
    try:
        if status is None:
            row = ledger.connection.execute(
                "SELECT COUNT(*) AS count FROM recording_jobs"
            ).fetchone()
        else:
            row = ledger.connection.execute(
                "SELECT COUNT(*) AS count FROM recording_jobs WHERE status = ?",
                (status,),
            ).fetchone()
        return int(row["count"])
    finally:
        ledger.close()


def _job_by_id(sqlite_path: Path, job_id: int) -> Optional[RecordingJob]:
    ledger = open_ledger(sqlite_path)
    try:
        return ledger.get_by_id(job_id)
    finally:
        ledger.close()


def _jobs_by_status(
    sqlite_path: Path,
    status: str,
    classification: Optional[str] = None,
) -> list[RecordingJob]:
    ledger = open_ledger(sqlite_path)
    try:
        params: tuple[object, ...]
        classification_clause = ""
        if classification is None:
            params = (status,)
        else:
            classification_clause = "AND classification = ?"
            params = (status, classification)
        rows = ledger.connection.execute(
            f"""
            SELECT checksum_sha256
            FROM recording_jobs
            WHERE status = ?
              {classification_clause}
            ORDER BY parsed_recorded_at, id
            """,
            params,
        ).fetchall()
        return [
            job
            for row in rows
            if (job := ledger.get_by_checksum(row["checksum_sha256"])) is not None
        ]
    finally:
        ledger.close()


def _latest_consolidated_log(sqlite_path: Path) -> Optional[LatestLogGroup]:
    ledger = open_ledger(sqlite_path)
    try:
        row = ledger.connection.execute(
            """
            SELECT daily_log_path, MAX(consolidated_at) AS latest_consolidated_at
            FROM recording_jobs
            WHERE status = 'consolidated'
              AND classification = 'log'
              AND daily_log_path IS NOT NULL
            GROUP BY daily_log_path
            ORDER BY latest_consolidated_at DESC, daily_log_path DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        jobs = _jobs_for_daily_log(ledger, row["daily_log_path"])
        return LatestLogGroup(
            daily_log_path=row["daily_log_path"],
            consolidated_at=row["latest_consolidated_at"],
            jobs=tuple(jobs),
        )
    finally:
        ledger.close()


def _jobs_for_daily_log(ledger, daily_log_path: str) -> list[RecordingJob]:
    rows = ledger.connection.execute(
        """
        SELECT checksum_sha256
        FROM recording_jobs
        WHERE status = 'consolidated'
          AND classification = 'log'
          AND daily_log_path = ?
        ORDER BY parsed_recorded_at, id
        """,
        (daily_log_path,),
    ).fetchall()
    return [
        job
        for row in rows
        if (job := ledger.get_by_checksum(row["checksum_sha256"])) is not None
    ]


def _job_summary(job: RecordingJob) -> JobSummary:
    return JobSummary(
        id=job.id,
        status=job.status,
        classification=job.classification,
        recorded_at=job.parsed_recorded_at,
        source_filename=job.source_filename,
        obsidian_path=job.obsidian_path,
        daily_log_path=job.daily_log_path,
        first_seen_at=job.first_seen_at,
        last_seen_at=job.last_seen_at,
    )


def _job_detail(job: RecordingJob) -> JobDetail:
    summary = _job_summary(job)
    summary_payload = (
        summary.model_dump()
        if hasattr(summary, "model_dump")
        else summary.dict()
    )
    return JobDetail(
        **summary_payload,
        checksum_prefix=job.checksum_sha256[:12],
        copied_at=job.copied_at,
        submitted_to_odin_at=job.submitted_to_odin_at,
        transcribed_at=job.transcribed_at,
        routed_at=job.routed_at,
        consolidated_at=job.consolidated_at,
        late_arrival_at=job.late_arrival_at,
        asr_model=job.asr_model,
    )


def _delete_after(recorded_at: Optional[str]) -> Optional[str]:
    if recorded_at is None:
        return None
    return (datetime.fromisoformat(recorded_at) + timedelta(days=28)).date().isoformat()
