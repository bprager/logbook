from __future__ import annotations

from dataclasses import dataclass

from logbook.checksum import sha256_file
from logbook.config import AppConfig
from logbook.ledger import open_ledger
from logbook.recorder import (
    RecorderAccessError,
    RecordingCandidate,
    RecorderValidation,
    discover_recordings,
    validate_recorder,
)


@dataclass(frozen=True)
class IngestDryRunItem:
    candidate: RecordingCandidate
    checksum_sha256: str
    ledger_status: str
    ledger_job_id: int | None


@dataclass(frozen=True)
class IngestDryRunResult:
    validation: RecorderValidation
    ledger_path: str
    ledger_written: bool
    items: tuple[IngestDryRunItem, ...]
    discovery_error: str | None = None

    @property
    def new_count(self) -> int:
        return sum(1 for item in self.items if item.ledger_status == "new")

    @property
    def known_count(self) -> int:
        return sum(1 for item in self.items if item.ledger_status == "known")


def run_ingest_dry_run(config: AppConfig, record_discovery: bool = False) -> IngestDryRunResult:
    validation = validate_recorder(config.recorder)
    if not validation.operational:
        return IngestDryRunResult(
            validation=validation,
            ledger_path=str(config.sqlite_path),
            ledger_written=False,
            items=(),
        )

    ledger_exists = config.sqlite_path.exists()
    ledger = open_ledger(config.sqlite_path, initialize=True) if record_discovery else None
    if ledger is None and ledger_exists:
        ledger = open_ledger(config.sqlite_path, initialize=False)

    try:
        try:
            candidates = discover_recordings(validation.recordings_dir)
        except RecorderAccessError as error:
            return IngestDryRunResult(
                validation=validation,
                ledger_path=str(config.sqlite_path),
                ledger_written=False,
                items=(),
                discovery_error=str(error),
            )
        items: list[IngestDryRunItem] = []
        for candidate in candidates:
            checksum = sha256_file(candidate.path)
            existing = ledger.get_by_checksum(checksum) if ledger else None
            if record_discovery and ledger:
                job = ledger.record_discovery(
                    candidate=candidate,
                    checksum_sha256=checksum,
                    source_device=config.recorder.volume_name,
                )
                status = "known" if existing else "new"
                job_id = job.id
            else:
                status = "known" if existing else "new"
                job_id = existing.id if existing else None

            items.append(
                IngestDryRunItem(
                    candidate=candidate,
                    checksum_sha256=checksum,
                    ledger_status=status,
                    ledger_job_id=job_id,
                )
            )
    finally:
        if ledger:
            ledger.close()

    return IngestDryRunResult(
        validation=validation,
        ledger_path=str(config.sqlite_path),
        ledger_written=record_discovery,
        items=tuple(items),
    )
