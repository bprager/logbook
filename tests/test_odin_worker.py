from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from fastapi.testclient import TestClient

from logbook.config import OdinConfig
from logbook.odin import OdinTranscriptResult, OdinTranscriptSegment
from logbook.odin_worker import OdinWorkerConfig, create_odin_worker_app


class OdinWorkerTests(TestCase):
    def test_worker_health_reports_model_readiness(self) -> None:
        with TemporaryDirectory() as tmp:
            app = create_odin_worker_app(
                OdinWorkerConfig(root=Path(tmp), odin=_odin_config()),
                transcriber=_FakeTranscriber(),
            )

            response = TestClient(app).get("/health")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["healthy"])
        self.assertEqual(payload["asr_model"], "large-v3")
        self.assertEqual(payload["status"], "ready")

    def test_worker_accepts_multipart_job_and_returns_result(self) -> None:
        with TemporaryDirectory() as tmp:
            app = create_odin_worker_app(
                OdinWorkerConfig(root=Path(tmp), odin=_odin_config()),
                transcriber=_FakeTranscriber(),
            )
            client = TestClient(app)

            submit = client.post(
                "/jobs",
                data={
                    "job_id": "17",
                    "checksum_sha256": "abc123",
                    "diarize": "false",
                },
                files={"audio": ("260429_0821.mp3", b"audio bytes", "audio/mpeg")},
            )
            result = client.get(f"/jobs/{submit.json()['odin_job_id']}/result")

        self.assertEqual(submit.status_code, 200)
        self.assertEqual(submit.json()["status"], "succeeded")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["text"], "log entry real worker transcript")
        self.assertEqual(result.json()["segments"][0]["start_seconds"], 0.0)


class _FakeTranscriber:
    @property
    def model_ready(self) -> bool:
        return True

    @property
    def detail(self) -> str:
        return "fake worker ready"

    def transcribe(
        self,
        audio_path: Path,
        *,
        odin_job_id: str,
        diarize: bool,
    ) -> OdinTranscriptResult:
        self.audio_path = audio_path
        return OdinTranscriptResult(
            odin_job_id=odin_job_id,
            status="succeeded",
            text="log entry real worker transcript",
            language="en",
            asr_model="large-v3",
            segments=(
                OdinTranscriptSegment(
                    start_seconds=0.0,
                    end_seconds=1.0,
                    text="log entry real worker transcript",
                ),
            ),
            diarization_model=None,
        )


def _odin_config() -> OdinConfig:
    return OdinConfig(
        api_base_url="http://odin.test",
        api_token=None,
        asr_model="large-v3",
        asr_device="cuda",
        asr_compute_type="float16",
        asr_vad_filter=True,
        diarization_model="pyannote/speaker-diarization-3.1",
    )
