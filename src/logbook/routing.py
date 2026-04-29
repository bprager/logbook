from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from logbook.classifier import PrefixClassification, classify_transcript
from logbook.config import AppConfig
from logbook.ledger import RecordingJob, open_ledger
from logbook.markdown import render_meeting_note, render_routed_note
from logbook.paths import (
    category_note_path,
    dead_letter_path,
    inbox_log_path,
    meeting_note_path,
    parse_recorded_at,
)
from logbook.vault import ObsidianVaultWorkflow, VaultWorkflowReport
from logbook.writers import FilesystemNoteWriter, NoteWriteError, NoteWriter


@dataclass(frozen=True)
class RoutingItem:
    job: RecordingJob
    status: str
    classification: PrefixClassification | None
    output_path: Path | None


@dataclass(frozen=True)
class RoutingResult:
    vault_root: Path
    items: tuple[RoutingItem, ...]
    vault_report: VaultWorkflowReport | None = None

    @property
    def routed_count(self) -> int:
        return sum(1 for item in self.items if item.status.endswith("_written"))

    @property
    def failed_count(self) -> int:
        return sum(1 for item in self.items if item.status.startswith("failed"))

    @property
    def log_count(self) -> int:
        return sum(1 for item in self.items if item.status == "inbox_written")

    @property
    def dead_letter_count(self) -> int:
        return sum(1 for item in self.items if item.status == "dead_letter_written")


def route_transcripts(
    config: AppConfig,
    vault_root: Path,
    vault_workflow: ObsidianVaultWorkflow | None = None,
    note_writer: NoteWriter | None = None,
    job_id: int | None = None,
    include_routed: bool = False,
    commit_message: str = "Update Logbook generated notes",
) -> RoutingResult:
    note_writer = note_writer or FilesystemNoteWriter()
    if vault_workflow is None:
        return _route_transcripts(
            config,
            vault_root,
            note_writer,
            job_id,
            include_routed,
            vault_report=None,
        )

    with vault_workflow.session(commit_message):
        result = _route_transcripts(
            config,
            vault_root,
            note_writer,
            job_id,
            include_routed,
            vault_report=None,
        )
    return RoutingResult(
        vault_root=result.vault_root,
        items=result.items,
        vault_report=vault_workflow.report(),
    )


def _route_transcripts(
    config: AppConfig,
    vault_root: Path,
    note_writer: NoteWriter,
    job_id: int | None,
    include_routed: bool,
    vault_report: VaultWorkflowReport | None,
) -> RoutingResult:
    ledger = open_ledger(config.sqlite_path, initialize=True)
    try:
        items: list[RoutingItem] = []
        for job in ledger.routing_jobs(job_id=job_id, include_routed=include_routed):
            item = _route_job(job, vault_root, note_writer)
            items.append(item)
            if item.classification is None or item.output_path is None:
                continue
            updated = ledger.mark_routed(
                checksum_sha256=job.checksum_sha256,
                classification=item.classification.label,
                obsidian_path=item.output_path.relative_to(vault_root),
                status=item.status,
            )
            items[-1] = RoutingItem(
                job=updated,
                status=item.status,
                classification=item.classification,
                output_path=item.output_path,
            )
    finally:
        ledger.close()

    return RoutingResult(vault_root=vault_root, items=tuple(items), vault_report=vault_report)


def _route_job(
    job: RecordingJob,
    vault_root: Path,
    note_writer: NoteWriter,
) -> RoutingItem:
    if not job.transcript_path:
        return RoutingItem(job, "failed_missing_transcript_path", None, None)
    transcript_path = _preferred_transcript_path(job)
    if not transcript_path.exists():
        return RoutingItem(job, "failed_missing_transcript", None, None)

    try:
        recorded_at = parse_recorded_at(job.parsed_recorded_at)
    except ValueError:
        return RoutingItem(job, "failed_missing_recorded_at", None, None)

    classification = classify_transcript(_transcript_text(transcript_path))
    classification = _without_fake_audio_reference(job, classification)
    if classification.route_kind == "meeting" and not job.diarization_path:
        return RoutingItem(job, "failed_missing_diarization", classification, None)
    output_path = _output_path(vault_root, recorded_at, classification, job.id)
    try:
        note_writer.write_note(
            output_path,
            _render_note(job, recorded_at, classification, transcript_path),
        )
    except NoteWriteError:
        return RoutingItem(job, "failed_note_write", classification, output_path)
    return RoutingItem(
        job=job,
        status=_status_for(classification),
        classification=classification,
        output_path=output_path,
    )


def _transcript_text(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return str(payload.get("text") or "")


def _render_note(
    job: RecordingJob,
    recorded_at: datetime,
    classification: PrefixClassification,
    transcript_path: Path,
) -> str:
    if classification.route_kind == "meeting":
        return render_meeting_note(job, recorded_at, classification, transcript_path)
    return render_routed_note(job, recorded_at, classification)


def _preferred_transcript_path(job: RecordingJob) -> Path:
    if job.diarization_path:
        return Path(job.diarization_path)
    if job.transcript_path:
        return Path(job.transcript_path)
    raise ValueError("recording job does not have transcript_path")


def _output_path(
    vault_root: Path,
    recorded_at: datetime,
    classification: PrefixClassification,
    job_id: int,
) -> Path:
    if classification.route_kind == "log":
        return inbox_log_path(vault_root, recorded_at, job_id)
    if classification.route_kind == "meeting":
        return meeting_note_path(vault_root, recorded_at, job_id)
    if classification.route_kind == "category" and classification.category:
        return category_note_path(vault_root, recorded_at, classification.category, job_id)
    return dead_letter_path(vault_root, recorded_at, job_id)


def _status_for(classification: PrefixClassification) -> str:
    if classification.route_kind == "log":
        return "inbox_written"
    if classification.route_kind == "meeting":
        return "meeting_written"
    if classification.route_kind == "category":
        return "category_written"
    return "dead_letter_written"


def _without_fake_audio_reference(
    job: RecordingJob,
    classification: PrefixClassification,
) -> PrefixClassification:
    if not job.asr_model or not job.asr_model.startswith("fake-"):
        return classification
    leaked = job.source_filename
    content = classification.content.replace(f" for {leaked}", "")
    return PrefixClassification(
        route_kind=classification.route_kind,
        category=classification.category,
        matched_alias=classification.matched_alias,
        content=content,
    )
