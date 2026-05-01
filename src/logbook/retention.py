from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from logbook.checksum import sha256_file
from logbook.config import AppConfig
from logbook.ledger import RecordingJob, open_ledger


FINAL_NOTE_STATUSES = {
    "category_written",
    "dead_letter_discarded",
    "dead_letter_written",
    "meeting_written",
    "consolidated",
}


@dataclass(frozen=True)
class CleanupPlanItem:
    job: RecordingJob
    cleanup_eligible_at: str | None
    eligible: bool
    blockers: tuple[str, ...]
    local_audio_exists: bool
    recorder_audio_exists: bool
    local_action: str
    recorder_action: str


@dataclass(frozen=True)
class CleanupPlan:
    items: tuple[CleanupPlanItem, ...]
    retention_hours: int

    @property
    def eligible_count(self) -> int:
        return sum(1 for item in self.items if item.eligible)

    @property
    def blocked_count(self) -> int:
        return sum(1 for item in self.items if not item.eligible)

    @property
    def local_pending_count(self) -> int:
        return sum(1 for item in self.items if item.local_action in {"delete", "trash"})

    @property
    def recorder_pending_count(self) -> int:
        return sum(1 for item in self.items if item.recorder_action == "delete")


def plan_audio_cleanup(
    config: AppConfig,
    now: datetime | None = None,
    persist_eligibility: bool = False,
) -> CleanupPlan:
    now = now or datetime.now(timezone.utc)
    ledger = open_ledger(config.sqlite_path, initialize=True)
    try:
        items = [
            _plan_job(config, job, now)
            for job in ledger.cleanup_candidate_jobs()
        ]
        if persist_eligibility:
            for item in items:
                if item.cleanup_eligible_at is not None:
                    ledger.mark_cleanup_eligible(
                        item.job.checksum_sha256,
                        item.cleanup_eligible_at,
                    )
    finally:
        ledger.close()

    return CleanupPlan(items=tuple(items), retention_hours=config.retention.hours)


def execute_audio_cleanup(
    config: AppConfig,
    include_recorder: bool = False,
    now: datetime | None = None,
) -> CleanupPlan:
    now = now or datetime.now(timezone.utc)
    plan = plan_audio_cleanup(config, now=now, persist_eligibility=True)
    ledger = open_ledger(config.sqlite_path, initialize=True)
    try:
        for item in plan.items:
            if not item.eligible:
                continue
            local_status = None
            recorder_status = None
            local_cleaned_at = None
            recorder_cleaned_at = None
            errors: list[str] = []

            if item.local_action in {"delete", "trash"}:
                try:
                    _verify_checksum(Path(item.job.copied_path or ""), item.job.checksum_sha256)
                    _cleanup_path(
                        Path(item.job.copied_path or ""),
                        mode=config.retention.cleanup_mode,
                        trash_root=config.processing_root / "trash" / "local-audio",
                        job_id=item.job.id,
                    )
                    local_status = "trashed" if item.local_action == "trash" else "deleted"
                    local_cleaned_at = now.isoformat(timespec="seconds")
                except OSError as error:
                    local_status = "failed"
                    errors.append(f"local audio cleanup failed: {error}")

            if include_recorder and item.recorder_action == "delete":
                try:
                    source_path = Path(item.job.source_path)
                    _assert_recorder_child(config.recorder.recordings_dir, source_path)
                    _verify_checksum(source_path, item.job.checksum_sha256)
                    source_path.unlink()
                    recorder_status = "deleted"
                    recorder_cleaned_at = now.isoformat(timespec="seconds")
                except OSError as error:
                    recorder_status = "failed"
                    errors.append(f"recorder audio cleanup failed: {error}")

            if local_status or recorder_status:
                ledger.record_cleanup_attempt(
                    checksum_sha256=item.job.checksum_sha256,
                    local_audio_cleanup_status=local_status,
                    recorder_audio_cleanup_status=recorder_status,
                    local_audio_cleaned_at=local_cleaned_at,
                    recorder_audio_cleaned_at=recorder_cleaned_at,
                    error="; ".join(errors) if errors else None,
                    attempted_at=now.isoformat(timespec="seconds"),
                )
    finally:
        ledger.close()
    return plan_audio_cleanup(config, now=now, persist_eligibility=True)


def _plan_job(config: AppConfig, job: RecordingJob, now: datetime) -> CleanupPlanItem:
    blockers = list(_blockers(job))
    eligible_at = _cleanup_eligible_at(config, job) if not blockers else None
    if eligible_at is not None and now < datetime.fromisoformat(eligible_at):
        blockers.append("retention_window_open")

    local_exists = bool(job.copied_path and Path(job.copied_path).exists())
    recorder_exists = bool(job.source_path and Path(job.source_path).exists())
    eligible = not blockers
    local_action = _local_action(config, job, eligible, local_exists)
    recorder_action = _recorder_action(job, eligible, recorder_exists)
    return CleanupPlanItem(
        job=job,
        cleanup_eligible_at=eligible_at,
        eligible=eligible,
        blockers=tuple(blockers),
        local_audio_exists=local_exists,
        recorder_audio_exists=recorder_exists,
        local_action=local_action,
        recorder_action=recorder_action,
    )


def _blockers(job: RecordingJob) -> tuple[str, ...]:
    blockers: list[str] = []
    if not job.transcript_path:
        blockers.append("missing_transcript")
    elif not Path(job.transcript_path).exists():
        blockers.append("missing_transcript_file")
    if job.status not in FINAL_NOTE_STATUSES:
        blockers.append("not_finalized")
    if job.status == "consolidated":
        if not job.daily_log_path:
            blockers.append("missing_daily_log_path")
        if not job.consolidated_at:
            blockers.append("missing_consolidated_at")
    elif not job.obsidian_path:
        blockers.append("missing_obsidian_path")
    if job.classification == "meeting" or job.status == "meeting_written":
        if not job.diarization_path:
            blockers.append("missing_diarization")
        elif not Path(job.diarization_path).exists():
            blockers.append("missing_diarization_file")
    if not job.vault_synced_at:
        blockers.append("missing_vault_sync")
    return tuple(blockers)


def _cleanup_eligible_at(config: AppConfig, job: RecordingJob) -> str:
    timestamps = [
        value
        for value in (
            job.transcribed_at,
            job.diarized_at,
            job.routed_at,
            job.consolidated_at,
            job.vault_synced_at,
        )
        if value
    ]
    latest = max(datetime.fromisoformat(value) for value in timestamps)
    return (latest + timedelta(hours=config.retention.hours)).isoformat(timespec="seconds")


def _local_action(
    config: AppConfig,
    job: RecordingJob,
    eligible: bool,
    exists: bool,
) -> str:
    if job.local_audio_cleanup_status in {"deleted", "trashed", "missing"}:
        return "none"
    if not eligible:
        return "blocked"
    if not exists:
        return "missing"
    return "trash" if config.retention.cleanup_mode.startswith("trash") else "delete"


def _recorder_action(job: RecordingJob, eligible: bool, exists: bool) -> str:
    if job.recorder_audio_cleanup_status in {"deleted", "missing"}:
        return "none"
    if not eligible:
        return "blocked"
    if not exists:
        return "missing"
    return "delete"


def _cleanup_path(path: Path, mode: str, trash_root: Path, job_id: int) -> None:
    if mode.startswith("trash"):
        trash_root.mkdir(parents=True, exist_ok=True)
        target = trash_root / f"job-{job_id:06d}-{path.name}"
        if target.exists():
            target.unlink()
        shutil.move(str(path), str(target))
    else:
        path.unlink()


def _verify_checksum(path: Path, expected_checksum: str) -> None:
    if not path.exists():
        raise FileNotFoundError(path)
    actual = sha256_file(path)
    if actual != expected_checksum:
        raise OSError(f"checksum mismatch for {path}")


def _assert_recorder_child(recordings_dir: Path, source_path: Path) -> None:
    try:
        source_path.resolve().relative_to(recordings_dir.resolve())
    except ValueError as error:
        raise OSError(f"source path is outside recorder recordings directory: {source_path}") from error
