from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from logbook.config import OdinConfig
from logbook.odin import (
    FakeOdinClient,
    HttpOdinClient,
    OdinSubmitRequest,
    transcript_result_from_json,
)


class OdinClientTests(TestCase):
    def test_fake_odin_client_returns_successful_transcript(self) -> None:
        with TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "260429_0821.mp3"
            audio_path.write_bytes(b"audio")
            client = FakeOdinClient(_odin_config())

            response = client.submit_transcription(
                OdinSubmitRequest(
                    job_id="17",
                    audio_path=audio_path,
                    checksum_sha256="abc123",
                )
            )
            result = client.get_result(response.odin_job_id)

            self.assertEqual(response.status, "succeeded")
            self.assertEqual(result.status, "succeeded")
            self.assertEqual(result.text, "log entry Placeholder transcript.")
            self.assertNotIn("260429_0821.mp3", result.text)
            self.assertEqual(result.asr_model, "fake-large-v3")

    def test_fake_odin_client_can_return_diarized_meeting_segments(self) -> None:
        with TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "260429_0821.mp3"
            audio_path.write_bytes(b"audio")
            client = FakeOdinClient(_odin_config())

            response = client.submit_transcription(
                OdinSubmitRequest(
                    job_id="17",
                    audio_path=audio_path,
                    checksum_sha256="abc123",
                    diarize=True,
                )
            )
            result = client.get_result(response.odin_job_id)

            self.assertEqual(result.text, "meeting Placeholder transcript.")
            self.assertEqual(result.diarization_model, "pyannote/speaker-diarization-3.1")
            self.assertEqual(
                tuple(segment.speaker for segment in result.segments),
                ("SPEAKER_00", "SPEAKER_01"),
            )

    def test_transcript_result_from_json_parses_segments(self) -> None:
        result = transcript_result_from_json(
            {
                "odin_job_id": "job-1",
                "status": "succeeded",
                "text": "hello",
                "language": "en",
                "asr_model": "large-v3",
                "diarization_model": None,
                "segments": [
                    {
                        "start_seconds": 0,
                        "end_seconds": 1.5,
                        "text": "hello",
                        "speaker": None,
                    }
                ],
            }
        )

        self.assertEqual(result.odin_job_id, "job-1")
        self.assertEqual(len(result.segments), 1)
        self.assertEqual(result.segments[0].end_seconds, 1.5)

    def test_http_client_health_parses_ready_payload(self) -> None:
        with patch("logbook.odin.request.urlopen") as urlopen:
            urlopen.return_value = _JsonResponse(
                {
                    "ready": True,
                    "status": "ready",
                    "asr_model": "large-v3",
                }
            )
            health = HttpOdinClient(_odin_config()).health()

        self.assertTrue(health.healthy)
        self.assertEqual(health.detail, "ready")
        self.assertEqual(health.payload["asr_model"], "large-v3")

    def test_http_client_submits_audio_and_reads_result(self) -> None:
        with TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "260429_0821.mp3"
            audio_path.write_bytes(b"audio")
            responses = [
                _JsonResponse({"odin_job_id": "odin-17", "status": "queued"}),
                _JsonResponse(
                    {
                        "odin_job_id": "odin-17",
                        "status": "succeeded",
                        "text": "log entry real transcript",
                        "language": "en",
                        "asr_model": "large-v3",
                        "diarization_model": None,
                        "segments": [
                            {
                                "start_seconds": 0,
                                "end_seconds": 1.0,
                                "text": "log entry real transcript",
                                "speaker": None,
                            }
                        ],
                    }
                ),
            ]
            with patch("logbook.odin.request.urlopen", side_effect=responses) as urlopen:
                client = HttpOdinClient(_odin_config())
                response = client.submit_transcription(
                    OdinSubmitRequest(
                        job_id="17",
                        audio_path=audio_path,
                        checksum_sha256="abc123",
                    )
                )
                result = client.get_result(response.odin_job_id)

        self.assertEqual(response.odin_job_id, "odin-17")
        self.assertEqual(response.status, "queued")
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.asr_model, "large-v3")
        self.assertEqual(urlopen.call_count, 2)

    def test_http_client_default_timeout_allows_long_meeting_jobs(self) -> None:
        with patch("logbook.odin.request.urlopen") as urlopen:
            urlopen.return_value = _JsonResponse({"ready": True})

            HttpOdinClient(_odin_config()).health()

        self.assertGreaterEqual(urlopen.call_args.kwargs["timeout"], 900)


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


class _JsonResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        import json

        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_JsonResponse":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        return False

    def read(self) -> bytes:
        return self.payload
