from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from logbook.classifier import PrefixClassification, classify_transcript
from logbook.config import AppConfig
from logbook.consolidation import ConsolidationResult, consolidate_daily_logs
from logbook.diarization import DiarizationResult, diarize_dead_letter_as_meeting
from logbook.entity_linker import EntityLinkResult, link_daily_log_entities
from logbook.ledger import ActionAudit, RecordingJob, open_ledger
from logbook.markdown import render_routed_note
from logbook.odin import OdinClient
from logbook.paths import inbox_log_path, meeting_note_path, parse_recorded_at
from logbook.routing import RoutingResult, route_transcripts
from logbook.writers import FilesystemNoteWriter, NoteWriteError, NoteWriter


@dataclass(frozen=True)
class DeadLetterListItem:
    job: RecordingJob
    recorded_at: datetime | None
    text_preview: str


@dataclass(frozen=True)
class DeadLetterListResult:
    items: tuple[DeadLetterListItem, ...]


@dataclass(frozen=True)
class DeadLetterManageResult:
    action: str
    job: RecordingJob | None
    status: str
    execute: bool
    target_route_kind: str | None = None
    inbox_path: Path | None = None
    meeting_path: Path | None = None
    removed_dead_letter_path: Path | None = None
    daily_log_path: Path | None = None
    consolidation: ConsolidationResult | None = None
    entity_links: EntityLinkResult | None = None
    diarization: DiarizationResult | None = None
    routing: RoutingResult | None = None
    audit: ActionAudit | None = None
    blockers: tuple[str, ...] = ()


def list_dead_letters(config: AppConfig) -> DeadLetterListResult:
    ledger = open_ledger(config.sqlite_path, initialize=True)
    try:
        items = [
            DeadLetterListItem(
                job=job,
                recorded_at=_try_recorded_at(job),
                text_preview=_preview_text(job),
            )
            for job in ledger.all_jobs()
            if job.status == "dead_letter_written" and job.classification == "dead_letter"
        ]
    finally:
        ledger.close()
    return DeadLetterListResult(items=tuple(items))


def assign_dead_letter_to_log(
    *,
    config: AppConfig,
    vault_root: Path,
    job_id: int,
    execute: bool,
    requested_by: str = "operator",
    reason: str | None = None,
    linker_months: int = 3,
    note_writer: NoteWriter | None = None,
    today: date | None = None,
) -> DeadLetterManageResult:
    note_writer = note_writer or FilesystemNoteWriter()
    ledger = open_ledger(config.sqlite_path, initialize=True)
    try:
        job = ledger.get_by_id(job_id)
        blockers = _assignment_blockers(job)
        if job is None:
            return DeadLetterManageResult("assign", None, "blocked", execute, "log", blockers=blockers)
        recorded_at = _try_recorded_at(job)
        if recorded_at is None:
            return DeadLetterManageResult("assign", job, "blocked", execute, "log", blockers=blockers)
        content = _rescued_log_content(job)
        inbox_path = inbox_log_path(vault_root, recorded_at, job.id)
        daily_path = _daily_path_for(vault_root, recorded_at)
        if blockers:
            return DeadLetterManageResult(
                "assign",
                job,
                "blocked",
                execute,
                "log",
                inbox_path=inbox_path,
                daily_log_path=daily_path,
                blockers=blockers,
            )
        if not execute:
            return DeadLetterManageResult(
                "assign",
                job,
                "would_assign",
                execute,
                "log",
                inbox_path=inbox_path,
                daily_log_path=daily_path,
            )

        try:
            note_writer.write_note(
                inbox_path,
                render_routed_note(
                    job,
                    recorded_at,
                    PrefixClassification(
                        route_kind="log",
                        category=None,
                        matched_alias="manual-dead-letter-rescue",
                        content=content,
                    ),
                ),
            )
        except NoteWriteError:
            return DeadLetterManageResult(
                "assign",
                job,
                "failed_note_write",
                execute,
                "log",
                inbox_path=inbox_path,
                daily_log_path=daily_path,
            )
        rescued = ledger.rescue_dead_letter_as_log(
            checksum_sha256=job.checksum_sha256,
            obsidian_path=inbox_path.relative_to(vault_root),
        )
        audit = ledger.record_action(
            action_type="dead_letter.assign",
            target_type="recording_job",
            target_id=str(job.id),
            requested_by=requested_by,
            request_payload={
                "reason": reason,
                "target_route_kind": "log",
                "previous_status": job.status,
                "previous_obsidian_path": job.obsidian_path,
                "new_obsidian_path": inbox_path.relative_to(vault_root).as_posix(),
            },
        )
    finally:
        ledger.close()

    consolidation = consolidate_daily_logs(
        config,
        vault_root=vault_root,
        note_writer=note_writer,
        entry_date=recorded_at.strftime("%Y-%m-%d"),
    )
    entity_links = link_daily_log_entities(
        vault_root=vault_root,
        months=linker_months,
        execute=True,
        today=today,
    )
    final_job = _load_job(config, job_id) or rescued
    final_daily_path = None
    if final_job.daily_log_path:
        final_daily_path = vault_root / final_job.daily_log_path
    return DeadLetterManageResult(
        "assign",
        final_job,
        "assigned",
        execute,
        "log",
        inbox_path=inbox_path,
        daily_log_path=final_daily_path or daily_path,
        consolidation=consolidation,
        entity_links=entity_links,
        audit=audit,
    )


def rescue_dead_letter_as_meeting(
    *,
    config: AppConfig,
    vault_root: Path,
    job_id: int,
    execute: bool,
    client: OdinClient,
    requested_by: str = "operator",
    reason: str | None = None,
) -> DeadLetterManageResult:
    ledger = open_ledger(config.sqlite_path, initialize=True)
    try:
        job = ledger.get_by_id(job_id)
        blockers = _meeting_rescue_blockers(job)
        if job is None:
            return DeadLetterManageResult(
                "rescue",
                None,
                "blocked",
                execute,
                "meeting",
                blockers=blockers,
            )
        recorded_at = _try_recorded_at(job)
        dead_letter_path = vault_root / job.obsidian_path if job.obsidian_path else None
        meeting_path = (
            meeting_note_path(vault_root, recorded_at, job.id) if recorded_at is not None else None
        )
        if blockers:
            return DeadLetterManageResult(
                "rescue",
                job,
                "blocked",
                execute,
                "meeting",
                meeting_path=meeting_path,
                removed_dead_letter_path=dead_letter_path,
                blockers=blockers,
            )
        if not execute:
            return DeadLetterManageResult(
                "rescue",
                job,
                "would_rescue",
                execute,
                "meeting",
                meeting_path=meeting_path,
                removed_dead_letter_path=dead_letter_path,
            )
    finally:
        ledger.close()

    diarization = diarize_dead_letter_as_meeting(config=config, client=client, job_id=job_id)
    if diarization.failed_count or not diarization.items or diarization.items[0].status != "diarized":
        status = diarization.items[0].status if diarization.items else "failed_diarization"
        return DeadLetterManageResult(
            "rescue",
            job,
            f"failed_diarization:{status}",
            execute,
            "meeting",
            meeting_path=meeting_path,
            removed_dead_letter_path=dead_letter_path,
            diarization=diarization,
        )

    routing = route_transcripts(config, vault_root, job_id=job_id)
    if routing.failed_count or not routing.items or routing.items[0].status != "meeting_written":
        status = routing.items[0].status if routing.items else "failed_route"
        return DeadLetterManageResult(
            "rescue",
            diarization.items[0].job,
            f"failed_route:{status}",
            execute,
            "meeting",
            meeting_path=meeting_path,
            removed_dead_letter_path=dead_letter_path,
            diarization=diarization,
            routing=routing,
        )

    routed_item = routing.items[0]
    final_meeting_path = routed_item.output_path or meeting_path
    removed_path = None
    if dead_letter_path is not None and dead_letter_path.exists():
        dead_letter_path.unlink()
        removed_path = dead_letter_path

    ledger = open_ledger(config.sqlite_path, initialize=True)
    try:
        final_job = ledger.get_by_id(job_id) or routed_item.job
        audit = ledger.record_action(
            action_type="dead_letter.rescue",
            target_type="recording_job",
            target_id=str(job_id),
            requested_by=requested_by,
            request_payload={
                "reason": reason,
                "target_route_kind": "meeting",
                "previous_status": job.status,
                "previous_obsidian_path": job.obsidian_path,
                "new_obsidian_path": (
                    final_meeting_path.relative_to(vault_root).as_posix()
                    if final_meeting_path is not None
                    else None
                ),
                "removed_dead_letter_path": (
                    removed_path.relative_to(vault_root).as_posix()
                    if removed_path is not None
                    else None
                ),
            },
        )
    finally:
        ledger.close()

    return DeadLetterManageResult(
        "rescue",
        final_job,
        "rescued",
        execute,
        "meeting",
        meeting_path=final_meeting_path,
        removed_dead_letter_path=removed_path,
        diarization=diarization,
        routing=routing,
        audit=audit,
    )


def discard_dead_letter(
    *,
    config: AppConfig,
    job_id: int,
    execute: bool,
    requested_by: str = "operator",
    reason: str | None = None,
) -> DeadLetterManageResult:
    ledger = open_ledger(config.sqlite_path, initialize=True)
    try:
        job = ledger.get_by_id(job_id)
        blockers = _dead_letter_blockers(job)
        if job is None:
            return DeadLetterManageResult("discard", None, "blocked", execute, blockers=blockers)
        if blockers:
            return DeadLetterManageResult("discard", job, "blocked", execute, blockers=blockers)
        if not execute:
            return DeadLetterManageResult("discard", job, "would_discard", execute)
        discarded = ledger.discard_dead_letter(job.checksum_sha256)
        audit = ledger.record_action(
            action_type="dead_letter.discard",
            target_type="recording_job",
            target_id=str(job.id),
            requested_by=requested_by,
            request_payload={
                "reason": reason,
                "previous_status": job.status,
                "previous_obsidian_path": job.obsidian_path,
            },
        )
        return DeadLetterManageResult(
            "discard",
            discarded,
            "discarded",
            execute,
            audit=audit,
        )
    finally:
        ledger.close()


def _assignment_blockers(job: RecordingJob | None) -> tuple[str, ...]:
    blockers = list(_dead_letter_blockers(job))
    if job is not None:
        if not job.transcript_path:
            blockers.append("missing_transcript_path")
        elif not Path(job.transcript_path).exists():
            blockers.append("missing_transcript_file")
        if _try_recorded_at(job) is None:
            blockers.append("missing_recorded_at")
    return tuple(blockers)


def _meeting_rescue_blockers(job: RecordingJob | None) -> tuple[str, ...]:
    blockers = list(_assignment_blockers(job))
    if job is not None:
        if not job.copied_path:
            blockers.append("missing_copied_path")
        elif not Path(job.copied_path).exists():
            blockers.append("missing_copied_audio")
    return tuple(blockers)


def _dead_letter_blockers(job: RecordingJob | None) -> tuple[str, ...]:
    if job is None:
        return ("job_not_found",)
    if job.status != "dead_letter_written" or job.classification != "dead_letter":
        return ("job_is_not_pending_dead_letter",)
    return ()


def _load_job(config: AppConfig, job_id: int) -> RecordingJob | None:
    ledger = open_ledger(config.sqlite_path, initialize=True)
    try:
        return ledger.get_by_id(job_id)
    finally:
        ledger.close()


def _preview_text(job: RecordingJob) -> str:
    try:
        content = _rescued_log_content(job)
    except (OSError, json.JSONDecodeError):
        return ""
    return " ".join(content.split())[:160]


def _rescued_log_content(job: RecordingJob) -> str:
    if not job.transcript_path:
        return ""
    payload = json.loads(Path(job.transcript_path).read_text(encoding="utf-8"))
    text = str(payload.get("text") or "")
    return classify_transcript(text).content.strip()


def _try_recorded_at(job: RecordingJob) -> datetime | None:
    try:
        return parse_recorded_at(job.parsed_recorded_at)
    except ValueError:
        return None


def _daily_path_for(vault_root: Path, recorded_at: datetime) -> Path:
    from logbook.paths import daily_log_path

    return daily_log_path(vault_root, recorded_at)
