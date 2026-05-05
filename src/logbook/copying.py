from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

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
class CopyItem:
    candidate: RecordingCandidate
    checksum_sha256: str
    status: str
    copied_path: Path | None
    ledger_job_id: int | None


@dataclass(frozen=True)
class CopyResult:
    validation: RecorderValidation
    inbox_dir: Path
    ledger_path: Path
    items: tuple[CopyItem, ...]
    discovery_error: str | None = None

    @property
    def copied_count(self) -> int:
        return sum(1 for item in self.items if item.status == "copied")

    @property
    def skipped_count(self) -> int:
        return sum(1 for item in self.items if item.status == "skipped_known_copied")

    @property
    def failed_count(self) -> int:
        item_failures = sum(1 for item in self.items if item.status.startswith("failed"))
        return item_failures + (1 if self.discovery_error else 0)


def copy_discovered_recordings(config: AppConfig) -> CopyResult:
    validation = validate_recorder(config.recorder)
    inbox_dir = config.processing_root / "inbox"
    if not validation.operational:
        return CopyResult(
            validation=validation,
            inbox_dir=inbox_dir,
            ledger_path=config.sqlite_path,
            items=(),
        )

    inbox_dir.mkdir(parents=True, exist_ok=True)
    ledger = open_ledger(config.sqlite_path, initialize=True)
    try:
        items: list[CopyItem] = []
        try:
            candidates = discover_recordings(validation.recordings_dir)
        except RecorderAccessError as error:
            return CopyResult(
                validation=validation,
                inbox_dir=inbox_dir,
                ledger_path=config.sqlite_path,
                items=(),
                discovery_error=str(error),
            )

        for candidate in candidates:
            checksum = sha256_file(candidate.path)
            job = ledger.get_by_checksum(checksum)
            if job is None:
                job = ledger.record_discovery(candidate, checksum, config.recorder.volume_name)

            if job.copied_path:
                copied_path = Path(job.copied_path)
                if copied_path.exists() and sha256_file(copied_path) == checksum:
                    items.append(
                        CopyItem(candidate, checksum, "skipped_known_copied", copied_path, job.id)
                    )
                    continue

            try:
                copied_path = _copy_with_checksum(candidate.path, inbox_dir, checksum)
            except OSError:
                items.append(CopyItem(candidate, checksum, "failed_copy_error", None, job.id))
                continue
            except ChecksumMismatchError:
                items.append(CopyItem(candidate, checksum, "failed_checksum_mismatch", None, job.id))
                continue

            copied_job = ledger.mark_copied(checksum, copied_path)
            items.append(CopyItem(candidate, checksum, "copied", copied_path, copied_job.id))
    finally:
        ledger.close()

    return CopyResult(
        validation=validation,
        inbox_dir=inbox_dir,
        ledger_path=config.sqlite_path,
        items=tuple(items),
    )


class ChecksumMismatchError(RuntimeError):
    pass


def _copy_with_checksum(source: Path, inbox_dir: Path, checksum: str) -> Path:
    target = _target_path(inbox_dir, source.name, checksum)
    if target.exists() and sha256_file(target) == checksum:
        return target

    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        # Copy bytes only. Some recorder volumes expose flags that macOS refuses
        # to apply to the destination, which makes shutil.copy2 fail in copystat.
        shutil.copyfile(source, tmp)
        copied_checksum = sha256_file(tmp)
        if copied_checksum != checksum:
            raise ChecksumMismatchError(
                f"checksum mismatch copying {source}: {copied_checksum} != {checksum}"
            )
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()

    return target


def _target_path(inbox_dir: Path, filename: str, checksum: str) -> Path:
    target = inbox_dir / filename
    if not target.exists():
        return target
    if sha256_file(target) == checksum:
        return target

    path = Path(filename)
    return inbox_dir / f"{path.stem}-{checksum[:12]}{path.suffix}"
