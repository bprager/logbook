from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from logbook.config import AppConfig
from logbook.ledger import RecordingJob, open_ledger
from logbook.odin import FakeOdinClient, OdinClient, OdinSubmitRequest


@dataclass(frozen=True)
class TranscriptionItem:
    job: RecordingJob
    status: str
    transcript_path: Path | None
    odin_job_id: str | None


@dataclass(frozen=True)
class TranscriptionResult:
    transcript_dir: Path
    items: tuple[TranscriptionItem, ...]

    @property
    def transcribed_count(self) -> int:
        return sum(1 for item in self.items if item.status == "transcribed")

    @property
    def skipped_count(self) -> int:
        return sum(1 for item in self.items if item.status == "skipped")

    @property
    def failed_count(self) -> int:
        return sum(1 for item in self.items if item.status.startswith("failed"))


def transcribe_copied_with_fake_odin(config: AppConfig) -> TranscriptionResult:
    return transcribe_copied(config=config, client=FakeOdinClient(config.odin))


def transcribe_copied(config: AppConfig, client: OdinClient) -> TranscriptionResult:
    transcript_dir = config.processing_root / "transcripts"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    ledger = open_ledger(config.sqlite_path, initialize=True)
    try:
        items: list[TranscriptionItem] = []
        for job in ledger.copied_jobs():
            if not job.copied_path:
                items.append(TranscriptionItem(job, "failed_missing_copied_path", None, None))
                continue
            audio_path = Path(job.copied_path)
            if not audio_path.exists():
                items.append(TranscriptionItem(job, "failed_missing_audio", None, None))
                continue

            transcript_path = transcript_dir / f"{audio_path.stem}.transcript.json"
            submit_response = client.submit_transcription(
                OdinSubmitRequest(
                    job_id=str(job.id),
                    audio_path=audio_path,
                    checksum_sha256=job.checksum_sha256,
                )
            )
            result = client.get_result(submit_response.odin_job_id)
            if result.status != "succeeded":
                items.append(
                    TranscriptionItem(
                        job,
                        f"failed_odin_{result.status}",
                        transcript_path,
                        submit_response.odin_job_id,
                    )
                )
                continue

            _write_transcript(transcript_path, result.to_json_dict())
            updated = ledger.mark_transcribed(
                checksum_sha256=job.checksum_sha256,
                odin_job_id=result.odin_job_id,
                transcript_path=transcript_path,
                asr_model=result.asr_model,
            )
            items.append(
                TranscriptionItem(
                    updated,
                    "transcribed",
                    transcript_path,
                    result.odin_job_id,
                )
            )
    finally:
        ledger.close()

    return TranscriptionResult(transcript_dir=transcript_dir, items=tuple(items))


def _write_transcript(path: Path, payload: dict[str, object]) -> None:
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)

