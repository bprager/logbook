from __future__ import annotations

import os
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

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
    attempt_count: int = 1

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


CopyProgressCallback = Callable[[int, int], None]


def copy_discovered_recordings(
    config: AppConfig,
    *,
    progress_callback: CopyProgressCallback | None = None,
) -> CopyResult:
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

        total_bytes = sum(candidate.size_bytes for candidate in candidates)
        copied_bytes = 0
        if progress_callback is not None:
            progress_callback(copied_bytes, total_bytes)

        def report_file_progress(file_copied_bytes: int, file_total_bytes: int) -> None:
            if progress_callback is not None:
                progress_callback(copied_bytes + file_copied_bytes, total_bytes)

        for candidate in candidates:
            checksum = sha256_file(candidate.path)
            job = ledger.get_by_checksum(checksum)
            if job is None:
                job = ledger.record_discovery(candidate, checksum, config.recorder.volume_name)

            if job.copied_path:
                copied_path = Path(job.copied_path)
                if copied_path.exists() and sha256_file(copied_path) == checksum:
                    copied_bytes += candidate.size_bytes
                    if progress_callback is not None:
                        progress_callback(copied_bytes, total_bytes)
                    items.append(
                        CopyItem(candidate, checksum, "skipped_known_copied", copied_path, job.id)
                    )
                    continue

            try:
                copied_path = _copy_with_checksum(
                    candidate.path,
                    inbox_dir,
                    checksum,
                    progress_callback=report_file_progress,
                )
            except OSError:
                items.append(CopyItem(candidate, checksum, "failed_copy_error", None, job.id))
                continue
            except ChecksumMismatchError:
                items.append(CopyItem(candidate, checksum, "failed_checksum_mismatch", None, job.id))
                continue

            copied_bytes += candidate.size_bytes
            if progress_callback is not None:
                progress_callback(copied_bytes, total_bytes)
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


def copy_discovered_recordings_with_retries(
    config: AppConfig,
    *,
    attempts: int = 24,
    delay_seconds: float = 15,
    sleep: Callable[[float], None] = time.sleep,
    progress_callback: CopyProgressCallback | None = None,
) -> CopyResult:
    attempts = max(1, attempts)
    result = copy_discovered_recordings(config, progress_callback=progress_callback)
    if not _should_retry(result):
        return result

    for attempt in range(2, attempts + 1):
        if delay_seconds > 0:
            sleep(delay_seconds)
        result = copy_discovered_recordings(config, progress_callback=progress_callback)
        if not _should_retry(result):
            return replace(result, attempt_count=attempt)

    return replace(result, attempt_count=attempts)


def _should_retry(result: CopyResult) -> bool:
    return result.discovery_error is not None or not result.validation.operational


class ChecksumMismatchError(RuntimeError):
    pass


def _copy_with_checksum(
    source: Path,
    inbox_dir: Path,
    checksum: str,
    *,
    progress_callback: CopyProgressCallback | None = None,
) -> Path:
    target = _target_path(inbox_dir, source.name, checksum)
    if target.exists() and sha256_file(target) == checksum:
        if progress_callback is not None:
            progress_callback(source.stat().st_size, source.stat().st_size)
        return target

    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        # Copy bytes only. Some recorder volumes expose flags that macOS refuses
        # to apply to the destination, which makes shutil.copy2 fail in copystat.
        _copy_file_bytes(source, tmp, progress_callback=progress_callback)
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


def _copy_file_bytes(
    source: Path,
    target: Path,
    *,
    progress_callback: CopyProgressCallback | None = None,
    chunk_size: int = 1024 * 1024,
) -> None:
    total = source.stat().st_size
    copied = 0
    with source.open("rb") as source_file, target.open("wb") as target_file:
        while True:
            chunk = source_file.read(chunk_size)
            if not chunk:
                break
            target_file.write(chunk)
            copied += len(chunk)
            if progress_callback is not None:
                progress_callback(copied, total)


def _target_path(inbox_dir: Path, filename: str, checksum: str) -> Path:
    target = inbox_dir / filename
    if not target.exists():
        return target
    if sha256_file(target) == checksum:
        return target

    path = Path(filename)
    return inbox_dir / f"{path.stem}-{checksum[:12]}{path.suffix}"
