from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from logbook.config import AppConfig, OdinConfig, RecorderConfig
from logbook.copying import copy_discovered_recordings
from logbook.ledger import open_ledger
from logbook.transcription import transcribe_copied_with_fake_odin


class TranscriptionTests(TestCase):
    def test_fake_transcribe_copied_writes_transcript_and_marks_ledger(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            _write_recording(app_config.recorder.recordings_dir / "260429_0821.mp3")
            copy_discovered_recordings(app_config)

            result = transcribe_copied_with_fake_odin(app_config)

            self.assertEqual(result.transcribed_count, 1)
            transcript_path = result.items[0].transcript_path
            self.assertIsNotNone(transcript_path)
            payload = json.loads(transcript_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "succeeded")
            self.assertEqual(payload["asr_model"], "fake-large-v3")

            ledger = open_ledger(app_config.sqlite_path)
            try:
                job = ledger.get_by_checksum(result.items[0].job.checksum_sha256)
            finally:
                ledger.close()

            self.assertIsNotNone(job)
            self.assertEqual(job.status, "transcribed")
            self.assertEqual(job.transcript_path, str(transcript_path))


def _app_config(root: Path) -> AppConfig:
    mount = root / "IC RECORDER"
    recordings_dir = mount / "REC_FILE" / "FOLDER01"
    recordings_dir.mkdir(parents=True)
    return AppConfig(
        processing_root=root / "VoiceIngest",
        sqlite_path=root / "VoiceIngest" / "voice_ingest.sqlite",
        recorder=RecorderConfig(
            volume_name="IC RECORDER",
            mount_path=mount,
            recordings_path="/REC_FILE/FOLDER01",
        ),
        odin=_odin_config(),
    )


def _write_recording(path: Path) -> None:
    path.write_bytes(b"fake mp3 bytes")
    timestamp = datetime(2026, 4, 29, 8, 21, 54).timestamp()
    os.utime(path, (timestamp, timestamp))


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

