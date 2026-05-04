from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from fastapi.testclient import TestClient

from logbook.config import OdinConfig
from logbook.odin import OdinTranscriptResult, OdinTranscriptSegment
from logbook.odin_worker import FasterWhisperTranscriber, OdinWorkerConfig, create_odin_worker_app


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

    def test_worker_metrics_report_readiness_and_job_counts_without_paths(self) -> None:
        with TemporaryDirectory() as tmp:
            worker_root = Path(tmp)
            app = create_odin_worker_app(
                OdinWorkerConfig(root=worker_root, odin=_odin_config()),
                transcriber=_FakeTranscriber(),
            )
            client = TestClient(app)

            client.post(
                "/jobs",
                data={
                    "job_id": "17",
                    "checksum_sha256": "abc123",
                    "diarize": "false",
                },
                files={"audio": ("260429_0821.mp3", b"audio bytes", "audio/mpeg")},
            )
            response = client.get("/metrics")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response.headers["content-type"])
        body = response.text
        self.assertIn("odin_worker_up 1", body)
        self.assertIn("odin_worker_model_ready 1", body)
        self.assertIn('odin_worker_jobs_by_status{status="succeeded"} 1', body)
        self.assertIn("odin_worker_jobs_in_memory 1", body)
        self.assertIn('odin_worker_model_info{asr_model="large-v3"', body)
        self.assertNotIn(str(worker_root), body)
        self.assertNotIn("260429_0821.mp3", body)

    def test_faster_whisper_transcriber_assigns_speakers_when_diarizing(self) -> None:
        with TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "meeting.mp3"
            audio_path.write_bytes(b"audio")
            transcriber = FasterWhisperTranscriber(_odin_config())
            transcriber._model = _FakeWhisperModel()
            transcriber._diarization_pipeline = _FakeDiarizationPipeline()

            result = transcriber.transcribe(audio_path, odin_job_id="odin-49", diarize=True)

        self.assertEqual(result.diarization_model, "pyannote/speaker-diarization-3.1")
        self.assertEqual(
            tuple(segment.speaker for segment in result.segments),
            ("SPEAKER_00", "SPEAKER_01"),
        )


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


class _FakeWhisperSegment:
    def __init__(self, start: float, end: float, text: str) -> None:
        self.start = start
        self.end = end
        self.text = text


class _FakeWhisperInfo:
    language = "en"


class _FakeWhisperModel:
    def transcribe(self, audio_path: str, *, vad_filter: bool, language: str | None):
        return (
            iter(
                (
                    _FakeWhisperSegment(0.0, 2.0, "Meeting first speaker."),
                    _FakeWhisperSegment(2.0, 4.0, "Second speaker."),
                )
            ),
            _FakeWhisperInfo(),
        )


class _FakeDiarizationSegment:
    def __init__(self, start: float, end: float) -> None:
        self.start = start
        self.end = end


class _FakeDiarizationAnnotation:
    def itertracks(self, *, yield_label: bool):
        return iter(
            (
                (_FakeDiarizationSegment(0.0, 2.1), "track-1", "SPEAKER_00"),
                (_FakeDiarizationSegment(2.1, 4.0), "track-2", "SPEAKER_01"),
            )
        )


class _FakeDiarizationPipeline:
    def __call__(self, audio_path: str):
        return _FakeDiarizationAnnotation()


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
