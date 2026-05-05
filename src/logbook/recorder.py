from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from logbook.config import RecorderConfig


SONY_MP3_RE = re.compile(
    r"^(?P<yy>\d{2})(?P<month>\d{2})(?P<day>\d{2})_"
    r"(?P<hour>\d{2})(?P<minute>\d{2})(?:_(?P<sequence>\d{2}))?\.mp3$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RecorderValidation:
    configured_mount_path: Path
    resolved_mount_path: Path
    recordings_dir: Path
    volume_name: str
    expected_volume_name: str
    exists: bool
    readable: bool
    writable: bool
    warnings: tuple[str, ...]

    @property
    def operational(self) -> bool:
        return self.exists and self.readable


@dataclass(frozen=True)
class RecordingCandidate:
    path: Path
    filename: str
    size_bytes: int
    modified_at: datetime
    parsed_recorded_at: datetime | None
    timestamp_matches_mtime: bool | None
    sequence: str | None


class RecorderAccessError(OSError):
    """Raised when the recorder folder exists but cannot be enumerated."""


def validate_recorder(config: RecorderConfig) -> RecorderValidation:
    warnings: list[str] = []
    resolved_mount_path = _resolve_mount_path(config, warnings)
    recordings_dir = resolved_mount_path / config.recordings_path.lstrip("/")
    volume_name = resolved_mount_path.name

    exists = recordings_dir.is_dir()
    readable = os.access(recordings_dir, os.R_OK)
    writable = os.access(recordings_dir, os.W_OK)

    if volume_name != config.volume_name:
        warnings.append(
            f"mounted volume name {volume_name!r} does not match expected {config.volume_name!r}"
        )
    if not exists:
        warnings.append(f"recordings directory does not exist: {recordings_dir}")
    elif not readable:
        warnings.append(f"recordings directory is not readable: {recordings_dir}")

    return RecorderValidation(
        configured_mount_path=config.mount_path,
        resolved_mount_path=resolved_mount_path,
        recordings_dir=recordings_dir,
        volume_name=volume_name,
        expected_volume_name=config.volume_name,
        exists=exists,
        readable=readable,
        writable=writable,
        warnings=tuple(warnings),
    )


def discover_recordings(recordings_dir: Path) -> list[RecordingCandidate]:
    candidates: list[RecordingCandidate] = []
    try:
        paths = sorted(recordings_dir.iterdir(), key=lambda item: item.name)
        for path in paths:
            if not _is_real_mp3(path):
                continue
            stat = path.stat()
            modified_at = datetime.fromtimestamp(stat.st_mtime)
            parsed_at, sequence = parse_sony_recording_name(path.name)
            candidates.append(
                RecordingCandidate(
                    path=path,
                    filename=path.name,
                    size_bytes=stat.st_size,
                    modified_at=modified_at,
                    parsed_recorded_at=parsed_at,
                    timestamp_matches_mtime=_timestamps_match(parsed_at, modified_at),
                    sequence=sequence,
                )
            )
    except OSError as error:
        raise RecorderAccessError(
            f"cannot read recordings directory: {recordings_dir}: {error}"
        ) from error
    return candidates


def parse_sony_recording_name(filename: str) -> tuple[datetime | None, str | None]:
    match = SONY_MP3_RE.match(filename)
    if not match:
        return None, None

    year = 2000 + int(match.group("yy"))
    try:
        return (
            datetime(
                year=year,
                month=int(match.group("month")),
                day=int(match.group("day")),
                hour=int(match.group("hour")),
                minute=int(match.group("minute")),
            ),
            match.group("sequence"),
        )
    except ValueError:
        return None, match.group("sequence")


def _resolve_mount_path(config: RecorderConfig, warnings: list[str]) -> Path:
    if not str(config.mount_path).startswith("/dev/"):
        return config.mount_path

    mount_point = _diskutil_mount_point(str(config.mount_path))
    if mount_point:
        warnings.append(
            f"SONY_RECORDER_MOUNT_PATH points to a device node; resolved to {mount_point}"
        )
        return Path(mount_point)

    warnings.append(
        "SONY_RECORDER_MOUNT_PATH points to a device node and no mount point could be resolved"
    )
    return config.mount_path


def _diskutil_mount_point(device_path: str) -> str | None:
    try:
        result = subprocess.run(
            ["diskutil", "info", device_path],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None

    if result.returncode != 0:
        return None

    for line in result.stdout.splitlines():
        if "Mount Point:" not in line:
            continue
        _, value = line.split(":", 1)
        mount_point = value.strip()
        return mount_point or None
    return None


def _is_real_mp3(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".mp3" and not path.name.startswith("._")


def _timestamps_match(parsed_at: datetime | None, modified_at: datetime) -> bool | None:
    if parsed_at is None:
        return None
    return (
        parsed_at.year == modified_at.year
        and parsed_at.month == modified_at.month
        and parsed_at.day == modified_at.day
        and parsed_at.hour == modified_at.hour
        and parsed_at.minute == modified_at.minute
    )
