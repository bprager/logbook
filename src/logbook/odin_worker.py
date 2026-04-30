from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, HTTPException, Request

from logbook.config import OdinConfig
from logbook.odin import OdinTranscriptResult, OdinTranscriptSegment


class WorkerTranscriber(Protocol):
    @property
    def model_ready(self) -> bool:
        raise NotImplementedError

    @property
    def detail(self) -> str | None:
        raise NotImplementedError

    def transcribe(
        self,
        audio_path: Path,
        *,
        odin_job_id: str,
        diarize: bool,
    ) -> OdinTranscriptResult:
        raise NotImplementedError


@dataclass(frozen=True)
class OdinWorkerConfig:
    root: Path
    odin: OdinConfig


def create_odin_worker_app(
    config: OdinWorkerConfig,
    transcriber: WorkerTranscriber | None = None,
) -> FastAPI:
    app = FastAPI(
        title="Logbook Odin Worker",
        version="0.1.0",
        description="Internal ASR worker for Logbook audio transcription.",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    worker = transcriber or FasterWhisperTranscriber(config.odin)
    jobs: dict[str, OdinTranscriptResult] = {}
    audio_root = config.root / "audio"
    audio_root.mkdir(parents=True, exist_ok=True)

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "ready": worker.model_ready,
            "healthy": worker.model_ready,
            "status": "ready" if worker.model_ready else "not_ready",
            "detail": worker.detail,
            "asr_model": config.odin.asr_model,
            "asr_device": config.odin.asr_device,
            "asr_compute_type": config.odin.asr_compute_type,
        }

    @app.post("/jobs")
    async def submit_job(request: Request) -> dict[str, str]:
        form = _parse_multipart(request.headers.get("content-type", ""), await request.body())
        job_id = form.fields.get("job_id")
        checksum = form.fields.get("checksum_sha256")
        if not job_id or not checksum:
            raise HTTPException(status_code=400, detail="job_id and checksum_sha256 are required")
        if form.audio is None:
            raise HTTPException(status_code=400, detail="audio file is required")

        odin_job_id = f"odin-{job_id}-{uuid.uuid4().hex[:12]}"
        audio_path = audio_root / f"{odin_job_id}-{form.audio.filename}"
        audio_path.write_bytes(form.audio.content)
        result = worker.transcribe(
            audio_path,
            odin_job_id=odin_job_id,
            diarize=form.fields.get("diarize", "false").lower() == "true",
        )
        jobs[odin_job_id] = result
        return {"odin_job_id": odin_job_id, "status": result.status}

    @app.get("/jobs/{odin_job_id}/result")
    def get_result(odin_job_id: str) -> dict[str, object]:
        result = jobs.get(odin_job_id)
        if result is None:
            raise HTTPException(status_code=404, detail="odin job not found")
        return result.to_json_dict()

    return app


class FasterWhisperTranscriber:
    def __init__(self, config: OdinConfig) -> None:
        self.config = config
        self._model = None
        self._detail: str | None = None

    @property
    def model_ready(self) -> bool:
        try:
            self._load_model()
        except Exception as error:
            self._detail = str(error)
            return False
        return True

    @property
    def detail(self) -> str | None:
        return self._detail

    def transcribe(
        self,
        audio_path: Path,
        *,
        odin_job_id: str,
        diarize: bool,
    ) -> OdinTranscriptResult:
        model = self._load_model()
        segment_iter, info = model.transcribe(
            str(audio_path),
            vad_filter=self.config.asr_vad_filter,
            language=os.environ.get("ODIN_ASR_LANGUAGE") or None,
        )
        raw_segments = tuple(segment_iter)
        segments = tuple(
            OdinTranscriptSegment(
                start_seconds=float(segment.start),
                end_seconds=float(segment.end),
                text=str(segment.text).strip(),
                speaker=None,
            )
            for segment in raw_segments
        )
        text = " ".join(segment.text for segment in segments).strip()
        return OdinTranscriptResult(
            odin_job_id=odin_job_id,
            status="succeeded",
            text=text,
            language=getattr(info, "language", None),
            asr_model=self.config.asr_model,
            diarization_model=self.config.diarization_model if diarize else None,
            segments=segments,
        )

    def _load_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self.config.asr_model,
                device=self.config.asr_device,
                compute_type=self.config.asr_compute_type,
            )
            self._detail = "model loaded"
        return self._model


@dataclass(frozen=True)
class _UploadedAudio:
    filename: str
    content: bytes


@dataclass(frozen=True)
class _ParsedMultipart:
    fields: dict[str, str]
    audio: _UploadedAudio | None


def _parse_multipart(content_type: str, body: bytes) -> _ParsedMultipart:
    if "multipart/form-data" not in content_type:
        raise HTTPException(status_code=415, detail="multipart/form-data required")
    message = BytesParser(policy=policy.default).parsebytes(
        (
            f"Content-Type: {content_type}\r\n"
            "MIME-Version: 1.0\r\n\r\n"
        ).encode("utf-8")
        + body
    )
    fields: dict[str, str] = {}
    audio: _UploadedAudio | None = None
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename is None:
            fields[name] = payload.decode(part.get_content_charset() or "utf-8")
        elif name == "audio":
            audio = _UploadedAudio(Path(filename).name, payload)
    return _ParsedMultipart(fields=fields, audio=audio)
