from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from logbook.config import OdinConfig
from logbook.odin import (
    FakeOdinClient,
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
