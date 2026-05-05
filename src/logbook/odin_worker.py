from __future__ import annotations

import os
import subprocess
import uuid
from collections import Counter
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse

from logbook.config import OdinConfig
from logbook.metrics import MetricSample, render_prometheus_metrics
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

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics() -> PlainTextResponse:
        return PlainTextResponse(
            _render_worker_metrics(config, worker, jobs),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

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


def _render_worker_metrics(
    config: OdinWorkerConfig,
    worker: WorkerTranscriber,
    jobs: dict[str, OdinTranscriptResult],
) -> str:
    status_counts = Counter(result.status for result in jobs.values())
    samples = [
        MetricSample(
            "odin_worker_up",
            1,
            help_text="Odin worker process is serving metrics.",
        ),
        MetricSample(
            "odin_worker_model_ready",
            1 if worker.model_ready else 0,
            help_text="Configured ASR model is loaded or loadable.",
        ),
        MetricSample(
            "odin_worker_jobs_in_memory",
            len(jobs),
            help_text="Odin job results retained in this worker process.",
        ),
        MetricSample(
            "odin_worker_model_info",
            1,
            labels={
                "asr_model": config.odin.asr_model,
                "device": config.odin.asr_device,
                "compute_type": config.odin.asr_compute_type,
            },
            help_text="Static configured Odin ASR model metadata.",
        ),
    ]
    for status, count in sorted(status_counts.items()):
        samples.append(
            MetricSample(
                "odin_worker_jobs_by_status",
                count,
                labels={"status": status},
                help_text="Odin worker jobs grouped by result status.",
            )
        )
    return render_prometheus_metrics(samples)


class FasterWhisperTranscriber:
    def __init__(self, config: OdinConfig) -> None:
        self.config = config
        self._model = None
        self._diarization_pipeline = None
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
        if diarize:
            segments = self._assign_speakers(audio_path, segments)
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

    def _assign_speakers(
        self,
        audio_path: Path,
        segments: tuple[OdinTranscriptSegment, ...],
    ) -> tuple[OdinTranscriptSegment, ...]:
        pipeline = self._load_diarization_pipeline()
        annotation = pipeline(str(prepare_diarization_audio(audio_path)))
        turns = tuple(_speaker_turns(annotation))
        if not turns:
            return segments
        return tuple(
            OdinTranscriptSegment(
                start_seconds=segment.start_seconds,
                end_seconds=segment.end_seconds,
                text=segment.text,
                speaker=_best_speaker_for_segment(segment, turns),
            )
            for segment in segments
        )

    def _load_diarization_pipeline(self):
        if self._diarization_pipeline is not None:
            return self._diarization_pipeline

        token = (
            self.config.huggingface_token
            or os.environ.get("HUGGINGFACE_TOKEN")
            or os.environ.get("HUGGING_FACE_TOKEN")
        )
        if not token:
            raise RuntimeError("HUGGINGFACE_TOKEN or HUGGING_FACE_TOKEN is required for diarization")

        from pyannote.audio import Pipeline

        try:
            pipeline = Pipeline.from_pretrained(self.config.diarization_model, token=token)
        except TypeError:
            pipeline = Pipeline.from_pretrained(
                self.config.diarization_model,
                use_auth_token=token,
            )

        try:
            import torch

            if self.config.asr_device == "cuda" and torch.cuda.is_available():
                pipeline.to(torch.device("cuda"))
        except Exception:
            pass

        self._diarization_pipeline = pipeline
        return self._diarization_pipeline


def prepare_diarization_audio(audio_path: Path) -> Path:
    if audio_path.suffix.lower() == ".wav":
        return audio_path

    target = audio_path.with_name(f"{audio_path.stem}.diarization.wav")
    if target.exists() and target.stat().st_mtime >= audio_path.stat().st_mtime:
        return target

    completed = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(audio_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            str(target),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        message = detail[-1] if detail else "ffmpeg failed"
        raise RuntimeError(f"failed to prepare diarization WAV for {audio_path.name}: {message}")
    return target


def _speaker_turns(output) -> tuple[tuple[float, float, str], ...]:
    for attribute in ("exclusive_speaker_diarization", "speaker_diarization"):
        diarization = getattr(output, attribute, None)
        if diarization is None:
            continue
        turns = tuple(_speaker_turns_from_diarization(diarization))
        if turns:
            return turns
    return tuple(_speaker_turns_from_diarization(output))


def _speaker_turns_from_diarization(diarization) -> tuple[tuple[float, float, str], ...]:
    if hasattr(diarization, "itertracks"):
        return tuple(
            (float(segment.start), float(segment.end), str(label))
            for segment, _track, label in diarization.itertracks(yield_label=True)
        )

    turns = []
    for item in diarization:
        values = tuple(item)
        if len(values) == 2:
            segment, label = values
        elif len(values) == 3:
            segment, _track, label = values
        else:
            continue
        turns.append((float(segment.start), float(segment.end), str(label)))
    return tuple(turns)


def _best_speaker_for_segment(
    segment: OdinTranscriptSegment,
    turns: tuple[tuple[float, float, str], ...],
) -> str | None:
    midpoint = (segment.start_seconds + segment.end_seconds) / 2
    best_label: str | None = None
    best_overlap = 0.0
    for start, end, label in turns:
        overlap = max(0.0, min(segment.end_seconds, end) - max(segment.start_seconds, start))
        if overlap > best_overlap:
            best_label = label
            best_overlap = overlap
    if best_label is not None:
        return best_label
    for start, end, label in turns:
        if start <= midpoint <= end:
            return label
    return None


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
