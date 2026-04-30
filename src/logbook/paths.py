from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path


MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def parse_recorded_at(value: str | None) -> datetime:
    if not value:
        raise ValueError("recording job does not have parsed_recorded_at")
    return datetime.fromisoformat(value)


def inbox_log_path(vault_root: Path, recorded_at: datetime, job_id: int) -> Path:
    date_part = recorded_at.strftime("%Y-%m-%d")
    timestamp_part = recorded_at.strftime("%Y-%m-%dT%H-%M-%S")
    return (
        vault_root
        / "10 - Logs"
        / "00 - Inbox"
        / recorded_at.strftime("%Y")
        / month_folder(recorded_at)
        / date_part
        / f"{timestamp_part}-job-{job_id:06d}-log-entry.md"
    )


def category_note_path(
    vault_root: Path,
    recorded_at: datetime,
    category: str,
    job_id: int,
) -> Path:
    timestamp_part = recorded_at.strftime("%Y-%m-%dT%H-%M-%S")
    return (
        vault_root
        / "20 - Notes"
        / "00 - Inbox"
        / category
        / f"{timestamp_part}-job-{job_id:06d}-{slugify(category)}.md"
    )


def meeting_note_path(vault_root: Path, recorded_at: datetime, job_id: int) -> Path:
    timestamp_part = recorded_at.strftime("%Y-%m-%dT%H-%M-%S")
    return (
        vault_root
        / "30 - Meetings"
        / recorded_at.strftime("%Y")
        / month_folder(recorded_at)
        / f"{timestamp_part}-job-{job_id:06d}-meeting.md"
    )


def dead_letter_path(vault_root: Path, recorded_at: datetime, job_id: int) -> Path:
    timestamp_part = recorded_at.strftime("%Y-%m-%dT%H-%M-%S")
    return vault_root / "99 - Dead Letters" / f"{timestamp_part}-job-{job_id:06d}.md"


def insight_review_path(vault_root: Path, recorded_at: datetime, job_id: int) -> Path:
    timestamp_part = recorded_at.strftime("%Y-%m-%dT%H-%M-%S")
    return (
        vault_root
        / "40 - Reviews"
        / "Logbook Insights"
        / recorded_at.strftime("%Y")
        / month_folder(recorded_at)
        / f"{timestamp_part}-job-{job_id:06d}-insights.md"
    )


def daily_log_path(vault_root: Path, recorded_at: datetime) -> Path:
    date_part = recorded_at.strftime("%Y-%m-%d")
    weekday = recorded_at.strftime("%A")
    return (
        vault_root
        / "06 - Timestamps"
        / recorded_at.strftime("%Y")
        / month_folder(recorded_at)
        / f"{date_part}-{weekday}-Log.md"
    )


def open_log_preview_path(vault_root: Path) -> Path:
    return vault_root / "10 - Logs" / "00 - Inbox" / "Open-Log-Preview.md"


def month_folder(value: datetime) -> str:
    return f"{value.month:02d}-{MONTH_NAMES[value.month - 1]}"


def slugify(value: str) -> str:
    lowered = value.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return slug or "note"
