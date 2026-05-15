from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from logbook.config import AppConfig, OdinConfig, RecorderConfig
from logbook.ingest import run_ingest_dry_run
from logbook.recorder import RecorderAccessError


class IngestDryRunTests(TestCase):
    def test_dry_run_does_not_create_ledger_without_record_flag(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            _write_recording(app_config.recorder.recordings_dir / "260429_0821.mp3")

            result = run_ingest_dry_run(app_config, record_discovery=False)

            self.assertEqual(len(result.items), 1)
            self.assertEqual(result.items[0].ledger_status, "new")
            self.assertFalse(app_config.sqlite_path.exists())
            self.assertFalse(result.ledger_written)

    def test_record_discovery_writes_ledger_and_second_dry_run_is_known(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            _write_recording(app_config.recorder.recordings_dir / "260429_0821.mp3")

            first = run_ingest_dry_run(app_config, record_discovery=True)
            second = run_ingest_dry_run(app_config, record_discovery=False)

            self.assertTrue(app_config.sqlite_path.exists())
            self.assertTrue(first.ledger_written)
            self.assertEqual(first.items[0].ledger_status, "new")
            self.assertEqual(second.items[0].ledger_status, "known")
            self.assertEqual(second.items[0].ledger_job_id, first.items[0].ledger_job_id)

    def test_dry_run_reports_recorder_access_error_without_crashing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)

            with patch(
                "logbook.ingest.discover_recordings",
                side_effect=RecorderAccessError("cannot read recordings directory: denied"),
            ):
                result = run_ingest_dry_run(app_config, record_discovery=False)

            self.assertEqual(result.items, ())
            self.assertEqual(result.discovery_error, "cannot read recordings directory: denied")
            self.assertFalse(result.ledger_written)


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
