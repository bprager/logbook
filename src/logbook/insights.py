from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from logbook.classifier import PrefixClassification, classify_transcript
from logbook.config import AppConfig
from logbook.ledger import RecordingJob, open_ledger, utc_now_iso
from logbook.markdown import render_insight_review
from logbook.paths import insight_review_path, parse_recorded_at
from logbook.writers import FilesystemNoteWriter, NoteWriteError, NoteWriter


ACTION_PATTERNS = (
    re.compile(r"\baction item\b", re.IGNORECASE),
    re.compile(r"\btodo\b", re.IGNORECASE),
    re.compile(r"\bto do\b", re.IGNORECASE),
    re.compile(r"\bfollow up\b", re.IGNORECASE),
    re.compile(r"\bneed to\b", re.IGNORECASE),
    re.compile(r"\bwe need\b", re.IGNORECASE),
    re.compile(r"\bi need\b", re.IGNORECASE),
    re.compile(r"\bremember to\b", re.IGNORECASE),
    re.compile(r"\bremind me to\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class InsightItem:
    job: RecordingJob
    status: str
    artifact_path: Path | None
    review_note_path: Path | None
    summary: str
    action_items: tuple[str, ...]


@dataclass(frozen=True)
class InsightResult:
    artifact_dir: Path
    vault_root: Path
    items: tuple[InsightItem, ...]

    @property
    def extracted_count(self) -> int:
        return sum(1 for item in self.items if item.status == "insight_review_written")

    @property
    def skipped_count(self) -> int:
        return sum(1 for item in self.items if item.status.startswith("skipped"))

    @property
    def failed_count(self) -> int:
        return sum(1 for item in self.items if item.status.startswith("failed"))


def extract_insights(
    config: AppConfig,
    vault_root: Path,
    note_writer: NoteWriter | None = None,
    job_id: int | None = None,
) -> InsightResult:
    note_writer = note_writer or FilesystemNoteWriter()
    artifact_dir = config.processing_root / "insights"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    ledger = open_ledger(config.sqlite_path, initialize=True)
    try:
        jobs = _candidate_jobs(ledger, job_id)
        items = tuple(
            _extract_job_insights(job, artifact_dir, vault_root, note_writer)
            for job in jobs
        )
    finally:
        ledger.close()
    return InsightResult(artifact_dir=artifact_dir, vault_root=vault_root, items=items)


def _candidate_jobs(ledger, job_id: int | None) -> list[RecordingJob]:
    if job_id is not None:
        job = ledger.get_by_id(job_id)
        return [job] if job is not None else []
    return [
        job
        for job in ledger.vault_sync_candidate_jobs()
        if job.status in {"consolidated", "category_written", "meeting_written"}
    ]


def _extract_job_insights(
    job: RecordingJob,
    artifact_dir: Path,
    vault_root: Path,
    note_writer: NoteWriter,
) -> InsightItem:
    source_path = _preferred_text_path(job)
    if source_path is None:
        return InsightItem(job, "failed_missing_transcript_path", None, None, "", ())
    if not source_path.exists():
        return InsightItem(job, "failed_missing_transcript", None, None, "", ())
    try:
        recorded_at = parse_recorded_at(job.parsed_recorded_at)
    except ValueError:
        return InsightItem(job, "failed_missing_recorded_at", None, None, "", ())

    text = _source_text(source_path)
    classification = _classification_for(job, text)
    if classification.route_kind == "dead_letter":
        return InsightItem(job, "skipped_dead_letter", None, None, "", ())

    summary = summarize_text(classification.content)
    actions = tuple(extract_action_items(classification.content, classification))
    artifact_path = artifact_dir / f"job-{job.id:06d}.insights.json"
    review_note_path = insight_review_path(vault_root, recorded_at, job.id)
    payload = {
        "job_id": job.id,
        "review_status": "needs_review",
        "canonical": False,
        "generated_at": utc_now_iso(),
        "route_kind": classification.route_kind,
        "category": classification.category,
        "source_note_path": job.obsidian_path or job.daily_log_path or "",
        "summary": summary,
        "action_items": list(actions),
    }
    _write_json(artifact_path, payload)
    try:
        note_writer.write_note(
            review_note_path,
            render_insight_review(
                job=job,
                recorded_at=recorded_at,
                route_kind=classification.route_kind,
                category=classification.category,
                summary=summary,
                action_items=list(actions),
                source_note_path=str(payload["source_note_path"]),
            ),
        )
    except NoteWriteError:
        return InsightItem(
            job,
            "failed_note_write",
            artifact_path,
            review_note_path,
            summary,
            actions,
        )
    return InsightItem(
        job,
        "insight_review_written",
        artifact_path,
        review_note_path,
        summary,
        actions,
    )


def summarize_text(text: str, max_chars: int = 280) -> str:
    sentences = _sentences(text)
    summary = " ".join(sentences[:2]) if sentences else text.strip()
    if len(summary) <= max_chars:
        return summary
    return summary[: max_chars - 1].rstrip() + "."


def extract_action_items(
    text: str,
    classification: PrefixClassification,
) -> list[str]:
    sentences = _sentences(text)
    if classification.category == "task":
        return sentences or [text.strip()] if text.strip() else []
    actions: list[str] = []
    for sentence in sentences:
        if any(pattern.search(sentence) for pattern in ACTION_PATTERNS):
            actions.append(sentence)
    return actions


def _classification_for(job: RecordingJob, text: str) -> PrefixClassification:
    classification = classify_transcript(text)
    if not job.asr_model or not job.asr_model.startswith("fake-"):
        return classification
    content = classification.content.replace(f" for {job.source_filename}", "")
    return PrefixClassification(
        route_kind=classification.route_kind,
        category=classification.category,
        matched_alias=classification.matched_alias,
        content=content,
    )


def _preferred_text_path(job: RecordingJob) -> Path | None:
    if job.diarization_path:
        return Path(job.diarization_path)
    if job.transcript_path:
        return Path(job.transcript_path)
    return None


def _source_text(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    segments = payload.get("segments")
    if isinstance(segments, list) and segments:
        texts = [
            str(segment.get("text") or "").strip()
            for segment in segments
            if isinstance(segment, dict)
        ]
        joined = " ".join(text for text in texts if text)
        if joined:
            return joined
    return str(payload.get("text") or "")


def _sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    parts = re.split(r"(?<=[.!?])\s+", normalized)
    return [part.strip(" -") for part in parts if part.strip(" -")]


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
