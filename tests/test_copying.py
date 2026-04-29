from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from logbook.config import AppConfig, OdinConfig, RecorderConfig
from logbook.copying import copy_discovered_recordings
from logbook.ledger import open_ledger


class CopyingTests(TestCase):
    def test_copy_discovered_recordings_verifies_and_marks_copied(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            source = app_config.recorder.recordings_dir / "260429_0821.mp3"
            _write_recording(source, b"audio")

            result = copy_discovered_recordings(app_config)

            self.assertEqual(result.copied_count, 1)
            self.assertEqual(result.failed_count, 0)
            copied_path = result.items[0].copied_path
            self.assertIsNotNone(copied_path)
            self.assertEqual(copied_path.read_bytes(), source.read_bytes())

            ledger = open_ledger(app_config.sqlite_path)
            try:
                job = ledger.get_by_checksum(result.items[0].checksum_sha256)
            finally:
                ledger.close()

            self.assertIsNotNone(job)
            self.assertEqual(job.status, "copied")
            self.assertEqual(job.copied_path, str(copied_path))

    def test_copy_discovered_recordings_is_idempotent(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            _write_recording(app_config.recorder.recordings_dir / "260429_0821.mp3", b"audio")

            first = copy_discovered_recordings(app_config)
            second = copy_discovered_recordings(app_config)

            self.assertEqual(first.copied_count, 1)
            self.assertEqual(second.copied_count, 0)
            self.assertEqual(second.skipped_count, 1)
            self.assertEqual(second.items[0].copied_path, first.items[0].copied_path)

    def test_copy_uses_checksum_suffix_for_name_collision(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            source = app_config.recorder.recordings_dir / "260429_0821.mp3"
            _write_recording(source, b"real audio")
            inbox = app_config.processing_root / "inbox"
            inbox.mkdir(parents=True)
            (inbox / "260429_0821.mp3").write_bytes(b"different audio")

            result = copy_discovered_recordings(app_config)

            self.assertEqual(result.copied_count, 1)
            self.assertNotEqual(result.items[0].copied_path, inbox / "260429_0821.mp3")
            self.assertIn("260429_0821-", result.items[0].copied_path.name)


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


def _write_recording(path: Path, content: bytes) -> None:
    path.write_bytes(content)
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
