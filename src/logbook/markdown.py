from __future__ import annotations

from datetime import datetime
from pathlib import Path

from logbook.classifier import PrefixClassification
from logbook.ledger import RecordingJob


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
    return f"{_frontmatter(frontmatter)}\n# {title}\n\n{classification.content.strip()}\n"


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
