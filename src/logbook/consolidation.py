from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from logbook.classifier import PrefixClassification, classify_transcript
from logbook.config import AppConfig
from logbook.ledger import Ledger, RecordingJob, open_ledger
from logbook.markdown import render_daily_log
from logbook.paths import daily_log_path, parse_recorded_at
from logbook.writers import FilesystemNoteWriter, NoteWriteError, NoteWriter


@dataclass(frozen=True)
class DailyLogEntry:
    job: RecordingJob
    recorded_at: datetime
    content: str


@dataclass(frozen=True)
class ConsolidationItem:
    entry_date: str
    status: str
    daily_log_path: Path | None
    entry_count: int


@dataclass(frozen=True)
class ConsolidationResult:
    vault_root: Path
    items: tuple[ConsolidationItem, ...]

    @property
    def consolidated_count(self) -> int:
        return sum(1 for item in self.items if item.status == "consolidated")

    @property
    def failed_count(self) -> int:
        return sum(1 for item in self.items if item.status.startswith("failed"))


def consolidate_daily_logs(
    config: AppConfig,
    vault_root: Path,
    note_writer: NoteWriter | None = None,
    entry_date: str | None = None,
) -> ConsolidationResult:
    note_writer = note_writer or FilesystemNoteWriter()
    ledger = open_ledger(config.sqlite_path, initialize=True)
    try:
        pending_jobs = ledger.log_jobs_for_consolidation(entry_date=entry_date)
        pending_dates = {
            parse_recorded_at(job.parsed_recorded_at).strftime("%Y-%m-%d")
            for job in pending_jobs
        }
        late_dates = {
            date_key
            for date_key in pending_dates
            if ledger.has_consolidated_log_for_date(date_key)
        }
        for job in pending_jobs:
            job_date = parse_recorded_at(job.parsed_recorded_at).strftime("%Y-%m-%d")
            if job_date in late_dates:
                ledger.mark_late_arrival(job.checksum_sha256)

        jobs = _jobs_for_render(ledger, pending_jobs, late_dates)
        grouped = _group_entries(jobs)
        items: list[ConsolidationItem] = []
        for date_key, entries in grouped.items():
            path = daily_log_path(vault_root, entries[0].recorded_at)
            source_dir = _source_dir(entries[0].recorded_at)
            content = render_daily_log(
                entry_date=date_key,
                entries=entries,
                generated_from=source_dir,
            )
            try:
                note_writer.write_note(path, content)
            except NoteWriteError:
                items.append(ConsolidationItem(date_key, "failed_note_write", path, len(entries)))
                continue

            relative_path = path.relative_to(vault_root)
            for entry in entries:
                ledger.mark_consolidated(
                    checksum_sha256=entry.job.checksum_sha256,
                    daily_log_path=relative_path,
                )
            items.append(ConsolidationItem(date_key, "consolidated", path, len(entries)))
    finally:
        ledger.close()

    return ConsolidationResult(vault_root=vault_root, items=tuple(items))


def _jobs_for_render(
    ledger: Ledger,
    pending_jobs: list[RecordingJob],
    late_dates: set[str],
) -> list[RecordingJob]:
    if not late_dates:
        return pending_jobs

    jobs_by_checksum = {job.checksum_sha256: job for job in pending_jobs}
    for entry_date in late_dates:
        for job in ledger.log_jobs_for_consolidation(
            entry_date=entry_date,
            include_consolidated=True,
        ):
            jobs_by_checksum[job.checksum_sha256] = job
    return list(jobs_by_checksum.values())


def _group_entries(jobs: list[RecordingJob]) -> dict[str, list[DailyLogEntry]]:
    grouped: dict[str, list[DailyLogEntry]] = defaultdict(list)
    for job in jobs:
        if not job.transcript_path:
            continue
        transcript_path = Path(job.transcript_path)
        if not transcript_path.exists():
            continue
        recorded_at = parse_recorded_at(job.parsed_recorded_at)
        classification = classify_transcript(_transcript_text(transcript_path))
        classification = _without_fake_audio_reference(job, classification)
        grouped[recorded_at.strftime("%Y-%m-%d")].append(
            DailyLogEntry(
                job=job,
                recorded_at=recorded_at,
                content=classification.content.strip(),
            )
        )

    for entries in grouped.values():
        entries.sort(key=lambda entry: (entry.recorded_at, entry.job.id))
    return dict(sorted(grouped.items()))


def _transcript_text(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return str(payload.get("text") or "")


def _source_dir(recorded_at: datetime) -> str:
    date_part = recorded_at.strftime("%Y-%m-%d")
    month_part = recorded_at.strftime("%m-%B")
    return f"10 - Logs/00 - Inbox/{recorded_at:%Y}/{month_part}/{date_part}"


def _without_fake_audio_reference(
    job: RecordingJob,
    classification: PrefixClassification,
) -> PrefixClassification:
    if not job.asr_model or not job.asr_model.startswith("fake-"):
        return classification
    content = classification.content.replace(f" for {job.source_filename}", "")
    return PrefixClassification(
        route_kind=classification.route_kind,
        category=classification.category,
        matched_alias=classification.matched_alias,
        content=content,
    )
