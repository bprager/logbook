import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from logbook.config import AppConfig
from logbook.ledger import MemoryActionReview, RecordingJob, open_ledger
from logbook.memory_graph import (
    MemoryGraphHealth,
    build_memory_graph_plan,
    check_memory_graph_health,
    query_memory_graph_plan,
)
from logbook.metrics import MetricSample, render_prometheus_metrics
from logbook.observer import build_observer_snapshot
from logbook.retention import plan_audio_cleanup


API_TITLE = "Logbook API"
API_VERSION = "1.1.0"


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
    diarized_at: Optional[str]
    vault_synced_at: Optional[str]
    cleanup_eligible_at: Optional[str]
    local_audio_cleanup_status: Optional[str]
    local_audio_cleaned_at: Optional[str]
    recorder_audio_cleanup_status: Optional[str]
    recorder_audio_cleaned_at: Optional[str]
    cleanup_attempt_count: int
    cleanup_last_attempt_at: Optional[str]
    cleanup_last_error: Optional[str]
    asr_model: Optional[str]
    diarization_model: Optional[str]


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


class CleanupStatusItem(BaseModel):
    job_id: int
    status: str
    classification: Optional[str]
    cleanup_eligible_at: Optional[str]
    eligible: bool
    blockers: list[str]
    local_audio_exists: bool
    recorder_audio_exists: bool
    local_action: str
    recorder_action: str
    local_audio_cleanup_status: Optional[str]
    recorder_audio_cleanup_status: Optional[str]
    cleanup_attempt_count: int
    cleanup_last_error: Optional[str]


class CleanupStatusResponse(BaseModel):
    count: int
    eligible_count: int
    blocked_count: int
    local_pending_count: int
    recorder_pending_count: int
    items: list[CleanupStatusItem]


class MemoryQueryItem(BaseModel):
    id: str
    label: Optional[str] = None
    name: Optional[str] = None
    text: Optional[str] = None
    job_id: Optional[int] = None
    review_status: Optional[str] = None
    recorded_at: Optional[str] = None
    resolved_at: Optional[str] = None
    resolved_by: Optional[str] = None
    resolution_note: Optional[str] = None


class MemoryQueryResponse(BaseModel):
    query: str
    count: int
    items: list[MemoryQueryItem]


class MemoryGraphCountItem(BaseModel):
    name: str
    planned: int
    live: Optional[int]
    drift: Optional[int]


class MemoryGraphHealthResponse(BaseModel):
    status: str = Field(
        description="ok when live Memgraph counts match the local plan; drift otherwise."
    )
    reachable: bool
    detail: Optional[str]
    planned_nodes: int
    live_nodes: Optional[int]
    planned_relationships: int
    live_relationships: Optional[int]
    labels: list[MemoryGraphCountItem]
    relationships: list[MemoryGraphCountItem]


class MemoryActionReviewResponse(BaseModel):
    action_id: str
    review_status: str
    resolved_at: Optional[str]
    resolved_by: str
    resolution_note: Optional[str]
    audit_id: int


class ObserverHealthResponse(BaseModel):
    api: str
    sqlite: str
    odin: str
    memgraph: str


class ObserverJobOutcomeResponse(BaseModel):
    job_id: int
    status: str
    classification: Optional[str]
    recorded_at: Optional[str]
    finished_at: str
    duration_seconds: Optional[int]
    vault_synced: bool


class ObserverFailureResponse(BaseModel):
    job_id: int
    status: str
    classification: Optional[str]
    occurred_at: str
    safe_detail: str


class ObserverStatsResponse(BaseModel):
    window: str
    jobs_seen: int
    succeeded: int
    failed: int
    dead_letters: int
    p50_duration_seconds: int
    p90_duration_seconds: int


class ObserverSnapshotResponse(BaseModel):
    generated_at: str
    health: ObserverHealthResponse
    current_run: Optional[dict]
    active_stage: Optional[dict]
    recent_finished: list[ObserverJobOutcomeResponse]
    recent_failures: list[ObserverFailureResponse]
    stats: ObserverStatsResponse


class ActionRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=500)
    requested_by: str = Field(default="openclaw", max_length=80)
    idempotency_key: Optional[str] = Field(default=None, max_length=120)


class DeadLetterRescueRequest(ActionRequest):
    target_route_kind: str = Field(default="log", pattern="^(log|category|meeting)$")
    target_category: Optional[str] = Field(default=None, max_length=80)


class ActionAcceptedResponse(BaseModel):
    audit_id: int
    action_type: str
    target_type: str
    target_id: str
    idempotency_key: Optional[str]
    status: str
    created_at: str


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

    def require_action_token(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    ) -> None:
        expected = config.api.action_token if config.api is not None else None
        if expected is None:
            raise HTTPException(status_code=503, detail="action token is not configured")
        if credentials is None or credentials.credentials != expected:
            raise HTTPException(status_code=401, detail="missing or invalid action token")

    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        summary="Local status and bounded action API for the Sony recorder to Obsidian pipeline.",
        description=(
            "This API exposes queue, routing, dead-letter, and consolidation status from the "
            "local SQLite ledger. Bounded action endpoints only record auditable action "
            "requests; they do not execute shell commands, delete files, or rewrite notes inline."
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
            {"name": "cleanup", "description": "Read-only audio retention cleanup status."},
            {"name": "memory", "description": "Proof-carrying local memory graph queries."},
            {"name": "observer", "description": "Compact read-only pipeline observer snapshot."},
            {"name": "actions", "description": "Token-protected bounded action requests."},
        ],
    )

    read_dependencies = [Depends(require_read_token)]
    action_dependencies = [Depends(require_action_token)]

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

    @app.get("/metrics", tags=["system"], response_class=PlainTextResponse)
    def metrics() -> PlainTextResponse:
        return PlainTextResponse(
            _render_logbook_metrics(config),
            media_type="text/plain; version=0.0.4; charset=utf-8",
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

    @app.get(
        "/cleanup/audio",
        tags=["cleanup"],
        response_model=CleanupStatusResponse,
        dependencies=read_dependencies,
    )
    def cleanup_audio_status() -> CleanupStatusResponse:
        plan = plan_audio_cleanup(config)
        return CleanupStatusResponse(
            count=len(plan.items),
            eligible_count=plan.eligible_count,
            blocked_count=plan.blocked_count,
            local_pending_count=plan.local_pending_count,
            recorder_pending_count=plan.recorder_pending_count,
            items=[
                CleanupStatusItem(
                    job_id=item.job.id,
                    status=item.job.status,
                    classification=item.job.classification,
                    cleanup_eligible_at=item.cleanup_eligible_at,
                    eligible=item.eligible,
                    blockers=list(item.blockers),
                    local_audio_exists=item.local_audio_exists,
                    recorder_audio_exists=item.recorder_audio_exists,
                    local_action=item.local_action,
                    recorder_action=item.recorder_action,
                    local_audio_cleanup_status=item.job.local_audio_cleanup_status,
                    recorder_audio_cleanup_status=item.job.recorder_audio_cleanup_status,
                    cleanup_attempt_count=item.job.cleanup_attempt_count,
                    cleanup_last_error=item.job.cleanup_last_error,
                )
                for item in plan.items
            ],
        )

    @app.get(
        "/memory/open-loops",
        tags=["memory"],
        response_model=MemoryQueryResponse,
        dependencies=read_dependencies,
    )
    def memory_open_loops() -> MemoryQueryResponse:
        return _memory_query_response(config, "open-loops")

    @app.get(
        "/memory/unresolved-actions",
        tags=["memory"],
        response_model=MemoryQueryResponse,
        dependencies=read_dependencies,
    )
    def memory_unresolved_actions() -> MemoryQueryResponse:
        return _memory_query_response(config, "unresolved-actions")

    @app.get(
        "/memory/recent-decisions",
        tags=["memory"],
        response_model=MemoryQueryResponse,
        dependencies=read_dependencies,
    )
    def memory_recent_decisions() -> MemoryQueryResponse:
        return _memory_query_response(config, "recent-decisions")

    @app.get(
        "/memory/topic-trails",
        tags=["memory"],
        response_model=MemoryQueryResponse,
        dependencies=read_dependencies,
    )
    def memory_topic_trails() -> MemoryQueryResponse:
        return _memory_query_response(config, "topic-trails")

    @app.get(
        "/memory/weekly-diff",
        tags=["memory"],
        response_model=MemoryQueryResponse,
        dependencies=read_dependencies,
    )
    def memory_weekly_diff() -> MemoryQueryResponse:
        return _memory_query_response(config, "weekly-diff")

    @app.get(
        "/memory/graph-health",
        tags=["memory"],
        response_model=MemoryGraphHealthResponse,
        dependencies=read_dependencies,
    )
    def memory_graph_health() -> MemoryGraphHealthResponse:
        return _memory_graph_health_response(check_memory_graph_health(config))

    @app.get(
        "/observer/snapshot",
        tags=["observer"],
        response_model=ObserverSnapshotResponse,
        dependencies=read_dependencies,
    )
    def observer_snapshot() -> ObserverSnapshotResponse:
        snapshot = build_observer_snapshot(config, probe_services=True)
        return ObserverSnapshotResponse(**snapshot.to_dict())

    @app.post(
        "/memory/actions/{action_id}/resolve",
        tags=["memory", "actions"],
        response_model=MemoryActionReviewResponse,
        status_code=202,
        dependencies=action_dependencies,
    )
    def resolve_memory_action(
        action_id: str,
        request: ActionRequest,
    ) -> MemoryActionReviewResponse:
        return _resolve_memory_action(config, action_id, request)

    @app.post(
        "/jobs/{job_id}/reprocess",
        tags=["actions"],
        response_model=ActionAcceptedResponse,
        status_code=202,
        dependencies=action_dependencies,
    )
    def request_job_reprocess(job_id: int, request: ActionRequest) -> ActionAcceptedResponse:
        job = _job_by_id(config.sqlite_path, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return _record_action(
            config.sqlite_path,
            action_type="job.reprocess",
            target_type="recording_job",
            target_id=str(job.id),
            requested_by=request.requested_by,
            idempotency_key=request.idempotency_key,
            payload={
                "reason": request.reason,
                "current_status": job.status,
                "classification": job.classification,
            },
        )

    @app.post(
        "/dead-letters/{job_id}/rescue",
        tags=["actions"],
        response_model=ActionAcceptedResponse,
        status_code=202,
        dependencies=action_dependencies,
    )
    def request_dead_letter_rescue(
        job_id: int,
        request: DeadLetterRescueRequest,
    ) -> ActionAcceptedResponse:
        job = _job_by_id(config.sqlite_path, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if job.status != "dead_letter_written" or job.classification != "dead_letter":
            raise HTTPException(status_code=409, detail="job is not a dead letter")
        if request.target_route_kind == "category" and not request.target_category:
            raise HTTPException(status_code=422, detail="target_category is required")
        return _record_action(
            config.sqlite_path,
            action_type="dead_letter.rescue",
            target_type="recording_job",
            target_id=str(job.id),
            requested_by=request.requested_by,
            idempotency_key=request.idempotency_key,
            payload={
                "reason": request.reason,
                "target_route_kind": request.target_route_kind,
                "target_category": request.target_category,
            },
        )

    @app.post(
        "/logs/{entry_date}/rebuild",
        tags=["actions"],
        response_model=ActionAcceptedResponse,
        status_code=202,
        dependencies=action_dependencies,
    )
    def request_log_rebuild(entry_date: str, request: ActionRequest) -> ActionAcceptedResponse:
        _validate_entry_date(entry_date)
        jobs_for_date = _log_jobs_for_date(config.sqlite_path, entry_date)
        if not jobs_for_date:
            raise HTTPException(status_code=404, detail="log date not found")
        return _record_action(
            config.sqlite_path,
            action_type="log.rebuild",
            target_type="log_date",
            target_id=entry_date,
            requested_by=request.requested_by,
            idempotency_key=request.idempotency_key,
            payload={
                "reason": request.reason,
                "job_ids": [job.id for job in jobs_for_date],
            },
        )

    return app


def _memory_query_response(config: AppConfig, query: str) -> MemoryQueryResponse:
    plan = build_memory_graph_plan(config)
    rows = query_memory_graph_plan(plan, query)
    return MemoryQueryResponse(
        query=query,
        count=len(rows),
        items=[MemoryQueryItem(**row) for row in rows],
    )


def _memory_graph_health_response(
    health: MemoryGraphHealth,
) -> MemoryGraphHealthResponse:
    labels = _graph_count_items(
        health.planned_counts_by_label,
        health.live_counts_by_label,
        health.drift_by_label,
    )
    relationships = _graph_count_items(
        health.planned_counts_by_relationship,
        health.live_counts_by_relationship,
        health.drift_by_relationship,
    )
    return MemoryGraphHealthResponse(
        status=health.status,
        reachable=health.reachable,
        detail=health.detail,
        planned_nodes=health.planned_nodes,
        live_nodes=health.live_nodes,
        planned_relationships=health.planned_relationships,
        live_relationships=health.live_relationships,
        labels=labels,
        relationships=relationships,
    )


def _graph_count_items(
    planned: dict[str, int],
    live: dict[str, int],
    drift: dict[str, int],
) -> list[MemoryGraphCountItem]:
    return [
        MemoryGraphCountItem(
            name=name,
            planned=planned.get(name, 0),
            live=live.get(name),
            drift=drift.get(name),
        )
        for name in sorted(set(planned) | set(live))
    ]


def _resolve_memory_action(
    config: AppConfig,
    action_id: str,
    request: ActionRequest,
) -> MemoryActionReviewResponse:
    plan = build_memory_graph_plan(config)
    candidate = next(
        (
            node
            for node in plan.nodes
            if node.id == action_id and "ActionCandidate" in node.labels
        ),
        None,
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="memory action candidate not found")

    ledger = open_ledger(config.sqlite_path, initialize=True)
    try:
        review = ledger.resolve_memory_action(
            action_id=action_id,
            resolved_by=request.requested_by,
            resolution_note=request.reason,
        )
        audit = ledger.record_action(
            action_type="memory.action.resolve",
            target_type="memory_action",
            target_id=action_id,
            requested_by=request.requested_by,
            idempotency_key=request.idempotency_key,
            request_payload={
                "reason": request.reason,
                "job_id": candidate.properties.get("job_id"),
                "text": candidate.properties.get("text"),
            },
        )
    finally:
        ledger.close()
    return _memory_action_review_response(review, audit_id=audit.id)


def _memory_action_review_response(
    review: MemoryActionReview,
    audit_id: int,
) -> MemoryActionReviewResponse:
    return MemoryActionReviewResponse(
        action_id=review.action_id,
        review_status=review.review_status,
        resolved_at=review.resolved_at,
        resolved_by=review.resolved_by,
        resolution_note=review.resolution_note,
        audit_id=audit_id,
    )


def _all_jobs(sqlite_path: Path) -> list[RecordingJob]:
    return _jobs(sqlite_path, status=None, limit=10_000, offset=0)


def _render_logbook_metrics(config: AppConfig) -> str:
    samples: list[MetricSample] = [
        MetricSample("logbook_up", 1, help_text="Logbook API process is serving metrics."),
    ]
    try:
        jobs = _all_jobs(config.sqlite_path)
    except sqlite3.Error:
        return render_prometheus_metrics(
            [
                MetricSample(
                    "logbook_up",
                    0,
                    help_text="Logbook API process is serving metrics.",
                ),
                MetricSample(
                    "logbook_sqlite_reachable",
                    0,
                    help_text="SQLite ledger can be opened and queried.",
                ),
            ]
        )

    status_counts = Counter(job.status for job in jobs)
    samples.extend(
        [
            MetricSample(
                "logbook_sqlite_reachable",
                1,
                help_text="SQLite ledger can be opened and queried.",
            ),
            MetricSample(
                "logbook_jobs_total",
                len(jobs),
                help_text="Total recording jobs in the local ledger.",
            ),
        ]
    )
    for status, count in sorted(status_counts.items()):
        samples.append(
            MetricSample(
                "logbook_jobs_by_status",
                count,
                labels={"status": status},
                help_text="Recording jobs grouped by ledger status.",
            )
        )
    samples.append(
        MetricSample(
            "logbook_dead_letters",
            status_counts.get("dead_letter_written", 0),
            help_text="Dead-letter jobs awaiting operator review.",
        )
    )
    samples.append(
        MetricSample(
            "logbook_open_log_entries",
            len(
                [
                    job
                    for job in jobs
                    if job.status == "inbox_written" and job.classification == "log"
                ]
            ),
            help_text="Log inbox entries waiting for daily consolidation.",
        )
    )
    latest = _latest_consolidated_log(config.sqlite_path)
    samples.append(
        MetricSample(
            "logbook_latest_consolidation_age_seconds",
            _age_seconds(latest.consolidated_at if latest is not None else None),
            help_text="Age of the latest canonical daily-log consolidation.",
        )
    )
    cleanup_plan = plan_audio_cleanup(config)
    samples.extend(
        [
            MetricSample(
                "logbook_cleanup_eligible",
                cleanup_plan.eligible_count,
                help_text="Jobs eligible for guarded source-audio cleanup.",
            ),
            MetricSample(
                "logbook_cleanup_blocked",
                cleanup_plan.blocked_count,
                help_text="Jobs blocked from source-audio cleanup.",
            ),
            MetricSample(
                "logbook_cleanup_local_pending",
                cleanup_plan.local_pending_count,
                help_text="Jobs with local copied-audio cleanup still pending.",
            ),
            MetricSample(
                "logbook_cleanup_recorder_pending",
                cleanup_plan.recorder_pending_count,
                help_text="Jobs with recorder-side cleanup still pending.",
            ),
        ]
    )
    graph_health = check_memory_graph_health(config)
    for status in ("ok", "drift", "unavailable", "not_configured"):
        samples.append(
            MetricSample(
                "logbook_memory_graph_health_status",
                1 if graph_health.status == status else 0,
                labels={"status": status},
                help_text="Memory graph health status as a one-hot gauge.",
            )
        )
    samples.append(
        MetricSample(
            "logbook_memory_graph_drift",
            0 if graph_health.status in {"ok", "not_configured"} else 1,
            help_text="One when live Memgraph differs from the local proof plan.",
        )
    )
    return render_prometheus_metrics(samples)


def _age_seconds(value: Optional[str]) -> int:
    if value is None:
        return 0
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - parsed).total_seconds()))


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


def _log_jobs_for_date(sqlite_path: Path, entry_date: str) -> list[RecordingJob]:
    ledger = open_ledger(sqlite_path)
    try:
        rows = ledger.connection.execute(
            """
            SELECT checksum_sha256
            FROM recording_jobs
            WHERE classification = 'log'
              AND status IN ('inbox_written', 'consolidated')
              AND substr(parsed_recorded_at, 1, 10) = ?
            ORDER BY parsed_recorded_at, id
            """,
            (entry_date,),
        ).fetchall()
        return [
            job
            for row in rows
            if (job := ledger.get_by_checksum(row["checksum_sha256"])) is not None
        ]
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
        diarized_at=job.diarized_at,
        vault_synced_at=job.vault_synced_at,
        cleanup_eligible_at=job.cleanup_eligible_at,
        local_audio_cleanup_status=job.local_audio_cleanup_status,
        local_audio_cleaned_at=job.local_audio_cleaned_at,
        recorder_audio_cleanup_status=job.recorder_audio_cleanup_status,
        recorder_audio_cleaned_at=job.recorder_audio_cleaned_at,
        cleanup_attempt_count=job.cleanup_attempt_count,
        cleanup_last_attempt_at=job.cleanup_last_attempt_at,
        cleanup_last_error=job.cleanup_last_error,
        asr_model=job.asr_model,
        diarization_model=job.diarization_model,
    )


def _delete_after(recorded_at: Optional[str]) -> Optional[str]:
    if recorded_at is None:
        return None
    return (datetime.fromisoformat(recorded_at) + timedelta(days=28)).date().isoformat()


def _record_action(
    sqlite_path: Path,
    action_type: str,
    target_type: str,
    target_id: str,
    requested_by: str,
    payload: dict,
    idempotency_key: Optional[str] = None,
) -> ActionAcceptedResponse:
    ledger = open_ledger(sqlite_path, initialize=True)
    try:
        audit = ledger.record_action(
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            request_payload=payload,
            requested_by=requested_by,
            idempotency_key=idempotency_key,
        )
    finally:
        ledger.close()
    return ActionAcceptedResponse(
        audit_id=audit.id,
        action_type=audit.action_type,
        target_type=audit.target_type,
        target_id=audit.target_id,
        idempotency_key=audit.idempotency_key,
        status=audit.status,
        created_at=audit.created_at,
    )


def _validate_entry_date(entry_date: str) -> None:
    try:
        datetime.strptime(entry_date, "%Y-%m-%d")
    except ValueError as error:
        raise HTTPException(status_code=422, detail="entry_date must be YYYY-MM-DD") from error
