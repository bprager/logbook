from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from logbook.classifier import classify_transcript
from logbook.config import AppConfig
from logbook.ledger import RecordingJob, open_ledger
from logbook.odin import FakeOdinClient, OdinClient, OdinSubmitRequest


@dataclass(frozen=True)
class DiarizationItem:
    job: RecordingJob
    status: str
    diarization_path: Path | None
    odin_job_id: str | None
    speaker_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiarizationResult:
    diarization_dir: Path
    items: tuple[DiarizationItem, ...]

    @property
    def diarized_count(self) -> int:
        return sum(1 for item in self.items if item.status == "diarized")

    @property
    def skipped_count(self) -> int:
        return sum(1 for item in self.items if item.status.startswith("skipped"))

    @property
    def failed_count(self) -> int:
        return sum(1 for item in self.items if item.status.startswith("failed"))


def diarize_meetings_with_fake_odin(config: AppConfig) -> DiarizationResult:
    return diarize_meetings(config=config, client=FakeOdinClient(config.odin))


def diarize_meetings(config: AppConfig, client: OdinClient) -> DiarizationResult:
    diarization_dir = config.processing_root / "diarization"
    diarization_dir.mkdir(parents=True, exist_ok=True)
    ledger = open_ledger(config.sqlite_path, initialize=True)
    try:
        items: list[DiarizationItem] = []
        for job in ledger.transcribed_jobs():
            item = _diarize_job(job, diarization_dir, client)
            items.append(item)
            if item.status != "diarized" or item.diarization_path is None:
                continue
            result_payload = _read_json(item.diarization_path)
            updated = ledger.mark_diarized(
                checksum_sha256=job.checksum_sha256,
                odin_job_id=item.odin_job_id or "",
                diarization_path=item.diarization_path,
                diarization_model=str(result_payload["diarization_model"]),
            )
            items[-1] = DiarizationItem(
                job=updated,
                status=item.status,
                diarization_path=item.diarization_path,
                odin_job_id=item.odin_job_id,
                speaker_labels=item.speaker_labels,
            )
    finally:
        ledger.close()

    return DiarizationResult(diarization_dir=diarization_dir, items=tuple(items))


def _diarize_job(
    job: RecordingJob,
    diarization_dir: Path,
    client: OdinClient,
) -> DiarizationItem:
    if not job.transcript_path:
        return DiarizationItem(job, "failed_missing_transcript_path", None, None)
    transcript_path = Path(job.transcript_path)
    if not transcript_path.exists():
        return DiarizationItem(job, "failed_missing_transcript", None, None)
    transcript_payload = _read_json(transcript_path)
    classification = classify_transcript(str(transcript_payload.get("text") or ""))
    if classification.route_kind != "meeting":
        return DiarizationItem(job, "skipped_not_meeting", None, None)
    if not job.copied_path:
        return DiarizationItem(job, "failed_missing_copied_path", None, None)
    audio_path = Path(job.copied_path)
    if not audio_path.exists():
        return DiarizationItem(job, "failed_missing_audio", None, None)

    diarization_path = diarization_dir / f"{audio_path.stem}.diarization.json"
    submit_response = client.submit_transcription(
        OdinSubmitRequest(
            job_id=str(job.id),
            audio_path=audio_path,
            checksum_sha256=job.checksum_sha256,
            diarize=True,
        )
    )
    result = client.get_result(submit_response.odin_job_id)
    if result.status != "succeeded":
        return DiarizationItem(
            job,
            f"failed_odin_{result.status}",
            diarization_path,
            submit_response.odin_job_id,
        )
    if not result.diarization_model:
        return DiarizationItem(
            job,
            "failed_missing_diarization_model",
            diarization_path,
            submit_response.odin_job_id,
        )

    speaker_labels = tuple(
        sorted({segment.speaker for segment in result.segments if segment.speaker})
    )
    if not speaker_labels:
        return DiarizationItem(
            job,
            "failed_missing_speaker_labels",
            diarization_path,
            submit_response.odin_job_id,
        )

    _write_json(diarization_path, result.to_json_dict())
    return DiarizationItem(
        job=job,
        status="diarized",
        diarization_path=diarization_path,
        odin_job_id=result.odin_job_id,
        speaker_labels=speaker_labels,
    )


def _read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, object]) -> None:
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
