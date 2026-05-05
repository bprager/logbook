from __future__ import annotations

import json
import mimetypes
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol
from urllib import request

from logbook.config import OdinConfig


@dataclass(frozen=True)
class OdinSubmitRequest:
    job_id: str
    audio_path: Path
    checksum_sha256: str
    diarize: bool = False


@dataclass(frozen=True)
class OdinSubmitResponse:
    odin_job_id: str
    status: str


@dataclass(frozen=True)
class OdinTranscriptSegment:
    start_seconds: float
    end_seconds: float
    text: str
    speaker: str | None = None


@dataclass(frozen=True)
class OdinTranscriptResult:
    odin_job_id: str
    status: str
    text: str
    language: str | None
    asr_model: str
    segments: tuple[OdinTranscriptSegment, ...]
    diarization_model: str | None = None

    def to_json_dict(self) -> dict[str, object]:
        return {
            "odin_job_id": self.odin_job_id,
            "status": self.status,
            "text": self.text,
            "language": self.language,
            "asr_model": self.asr_model,
            "diarization_model": self.diarization_model,
            "segments": [asdict(segment) for segment in self.segments],
        }


@dataclass(frozen=True)
class OdinHealth:
    healthy: bool
    detail: str | None
    payload: dict[str, object]


class OdinClient(Protocol):
    def submit_transcription(self, submit_request: OdinSubmitRequest) -> OdinSubmitResponse:
        raise NotImplementedError

    def get_result(self, odin_job_id: str) -> OdinTranscriptResult:
        raise NotImplementedError


class FakeOdinClient:
    def __init__(self, config: OdinConfig) -> None:
        self.config = config
        self._results: dict[str, OdinTranscriptResult] = {}

    def submit_transcription(self, submit_request: OdinSubmitRequest) -> OdinSubmitResponse:
        odin_job_id = f"fake-{submit_request.job_id}"
        text = (
            "meeting Placeholder transcript."
            if submit_request.diarize
            else "log entry Placeholder transcript."
        )
        segments = (
            (
                OdinTranscriptSegment(
                    start_seconds=0.0,
                    end_seconds=1.0,
                    text="meeting Placeholder",
                    speaker="SPEAKER_00",
                ),
                OdinTranscriptSegment(
                    start_seconds=1.0,
                    end_seconds=2.0,
                    text="transcript.",
                    speaker="SPEAKER_01",
                ),
            )
            if submit_request.diarize
            else (
                OdinTranscriptSegment(
                    start_seconds=0.0,
                    end_seconds=1.0,
                    text=text,
                    speaker=None,
                ),
            )
        )
        result = OdinTranscriptResult(
            odin_job_id=odin_job_id,
            status="succeeded",
            text=text,
            language="en",
            asr_model=f"fake-{self.config.asr_model}",
            diarization_model=self.config.diarization_model if submit_request.diarize else None,
            segments=segments,
        )
        self._results[odin_job_id] = result
        return OdinSubmitResponse(odin_job_id=odin_job_id, status="succeeded")

    def get_result(self, odin_job_id: str) -> OdinTranscriptResult:
        return self._results[odin_job_id]


class HttpOdinClient:
    def __init__(self, config: OdinConfig, timeout_seconds: float = 900.0) -> None:
        self.config = config
        self.timeout_seconds = timeout_seconds

    def health(self) -> OdinHealth:
        headers = {"Accept": "application/json"}
        if self.config.api_token:
            headers["Authorization"] = f"Bearer {self.config.api_token}"
        http_request = request.Request(
            f"{self.config.api_base_url}/health",
            headers=headers,
            method="GET",
        )
        with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            return OdinHealth(False, "health response was not a JSON object", {})
        healthy = bool(
            payload.get("healthy", payload.get("ok", payload.get("ready", False)))
        )
        detail = payload.get("detail") or payload.get("status")
        return OdinHealth(
            healthy=healthy,
            detail=str(detail) if detail is not None else None,
            payload=payload,
        )

    def submit_transcription(self, submit_request: OdinSubmitRequest) -> OdinSubmitResponse:
        boundary = f"----logbook-{uuid.uuid4().hex}"
        body = _multipart_body(
            boundary=boundary,
            fields={
                "job_id": submit_request.job_id,
                "checksum_sha256": submit_request.checksum_sha256,
                "diarize": "true" if submit_request.diarize else "false",
            },
            file_field="audio",
            file_path=submit_request.audio_path,
        )
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        }
        if self.config.api_token:
            headers["Authorization"] = f"Bearer {self.config.api_token}"

        http_request = request.Request(
            f"{self.config.api_base_url}/jobs",
            data=body,
            headers=headers,
            method="POST",
        )
        with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return OdinSubmitResponse(
            odin_job_id=str(payload["odin_job_id"]),
            status=str(payload["status"]),
        )

    def get_result(self, odin_job_id: str) -> OdinTranscriptResult:
        headers = {"Accept": "application/json"}
        if self.config.api_token:
            headers["Authorization"] = f"Bearer {self.config.api_token}"
        http_request = request.Request(
            f"{self.config.api_base_url}/jobs/{odin_job_id}/result",
            headers=headers,
            method="GET",
        )
        with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return transcript_result_from_json(payload)


def transcript_result_from_json(payload: dict[str, object]) -> OdinTranscriptResult:
    segments = tuple(
        OdinTranscriptSegment(
            start_seconds=float(segment["start_seconds"]),
            end_seconds=float(segment["end_seconds"]),
            text=str(segment["text"]),
            speaker=str(segment["speaker"]) if segment.get("speaker") is not None else None,
        )
        for segment in payload.get("segments", [])
        if isinstance(segment, dict)
    )
    return OdinTranscriptResult(
        odin_job_id=str(payload["odin_job_id"]),
        status=str(payload["status"]),
        text=str(payload["text"]),
        language=str(payload["language"]) if payload.get("language") is not None else None,
        asr_model=str(payload["asr_model"]),
        diarization_model=(
            str(payload["diarization_model"])
            if payload.get("diarization_model") is not None
            else None
        ),
        segments=segments,
    )


def _multipart_body(
    boundary: str,
    fields: dict[str, str],
    file_field: str,
    file_path: Path,
) -> bytes:
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"),
                f"{value}\r\n".encode("utf-8"),
            ]
        )

    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    chunks.extend(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{file_path.name}"\r\n'
            ).encode("utf-8"),
            f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
            file_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    return b"".join(chunks)
