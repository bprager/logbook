from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from logbook.classifier import PrefixClassification
from logbook.ledger import RecordingJob


class DailyLogEntryLike(Protocol):
    job: RecordingJob
    recorded_at: datetime
    content: str


def render_routed_note(
    job: RecordingJob,
    recorded_at: datetime,
    classification: PrefixClassification,
) -> str:
    title = _title_for(classification, recorded_at)
    frontmatter = {
        "type": classification.route_kind,
        "category": classification.category,
        "recorded_at": recorded_at.isoformat(timespec="seconds"),
        "source": "sony-icd-px370",
        "job_id": str(job.id),
        "checksum_sha256": job.checksum_sha256,
        "odin_job_id": job.odin_job_id or "",
        "asr_model": job.asr_model or "",
        "audio_retention": "source audio retained outside Obsidian until retention gate",
    }
    if classification.route_kind == "dead_letter":
        frontmatter["review_status"] = "needs_review"
        frontmatter["delete_after"] = (recorded_at + timedelta(days=28)).date().isoformat()

    sections = [_frontmatter(frontmatter), f"# {title}", classification.content.strip()]
    if classification.route_kind == "dead_letter":
        sections.extend(
            [
                "## Review",
                "- Status: needs_review\n- Rescue action: reroute after assigning a supported prefix",
            ]
        )
    return "\n\n".join(sections).rstrip() + "\n"


def render_meeting_note(
    job: RecordingJob,
    recorded_at: datetime,
    classification: PrefixClassification,
    diarization_path: Path,
) -> str:
    payload = json.loads(diarization_path.read_text(encoding="utf-8"))
    segments = [
        segment
        for segment in payload.get("segments", [])
        if isinstance(segment, dict)
    ]
    speakers = sorted(
        {
            str(segment["speaker"])
            for segment in segments
            if segment.get("speaker")
        }
    )
    if not speakers:
        raise ValueError("meeting note requires at least one speaker label")

    title = _title_for(classification, recorded_at)
    frontmatter = {
        "type": "meeting",
        "recorded_at": recorded_at.isoformat(timespec="seconds"),
        "source": "sony-icd-px370",
        "job_id": str(job.id),
        "checksum_sha256": job.checksum_sha256,
        "odin_job_id": job.odin_job_id or str(payload.get("odin_job_id") or ""),
        "asr_model": job.asr_model or str(payload.get("asr_model") or ""),
        "diarization_model": job.diarization_model or str(payload.get("diarization_model") or ""),
        "speaker_count": str(len(speakers)),
        "audio_retention": "source audio retained outside Obsidian until retention gate",
    }
    sections = [
        _frontmatter(frontmatter),
        f"# {title}",
        "## Participants",
        "\n".join(f"- {speaker}: " for speaker in speakers),
        "## Summary",
        "- ",
        "## Decisions",
        "- ",
        "## Action Items",
        "- ",
        "## Transcript",
        _render_meeting_transcript(segments, classification.matched_alias),
        f"_Source: `logbook-job-{job.id}`_",
    ]
    return "\n\n".join(sections).rstrip() + "\n"


def render_daily_log(
    entry_date: str,
    entries: list[DailyLogEntryLike],
    generated_from: str,
) -> str:
    if not entries:
        raise ValueError("daily log requires at least one entry")

    recorded_at = entries[0].recorded_at
    title = recorded_at.strftime("%A, %B %-d, %Y Log")
    frontmatter = {
        "type": "daily_log",
        "date": entry_date,
        "source": "voice_ingest",
        "entry_count": str(len(entries)),
        "generated_from": generated_from,
    }
    sections = [_frontmatter(frontmatter), f"# {title}"]
    for entry in entries:
        sections.extend(
            [
                f"## {entry.recorded_at:%H:%M}",
                entry.content.strip(),
                f"_Source: `logbook-job-{entry.job.id}`_",
            ]
        )
    return "\n\n".join(sections).rstrip() + "\n"


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _title_for(classification: PrefixClassification, recorded_at: datetime) -> str:
    timestamp = recorded_at.strftime("%Y-%m-%d %H:%M")
    if classification.route_kind == "log":
        return f"Log entry {timestamp}"
    if classification.route_kind == "meeting":
        return f"Meeting note {timestamp}"
    if classification.category:
        return f"{classification.category.title()} note {timestamp}"
    return f"Dead letter {timestamp}"


def _frontmatter(values: dict[str, str | None]) -> str:
    lines = ["---"]
    for key, value in values.items():
        if value is None:
            continue
        escaped = value.replace('"', '\\"')
        lines.append(f'{key}: "{escaped}"')
    lines.append("---")
    return "\n".join(lines)


def _render_meeting_transcript(segments: list[dict], matched_alias: str) -> str:
    lines: list[str] = []
    for index, segment in enumerate(segments):
        speaker = str(segment.get("speaker") or "SPEAKER_UNKNOWN")
        start_seconds = float(segment.get("start_seconds") or 0)
        text = str(segment.get("text") or "").strip()
        if index == 0:
            text = _strip_meeting_prefix(text, matched_alias)
        if not text:
            continue
        lines.append(f"- `{_format_timestamp(start_seconds)}` **{speaker}:** {text}")
    return "\n".join(lines)


def _strip_meeting_prefix(text: str, matched_alias: str) -> str:
    lowered = text.lower()
    prefix = matched_alias.lower()
    if lowered == prefix:
        return ""
    if lowered.startswith(f"{prefix} "):
        return text[len(matched_alias) :].strip()
    return text


def _format_timestamp(seconds: float) -> str:
    total_seconds = int(seconds)
    minutes, remainder = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{remainder:02d}"
    return f"{minutes:02d}:{remainder:02d}"
