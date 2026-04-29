from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from logbook.config import AppConfig, OdinConfig, RecorderConfig
from logbook.consolidation import consolidate_daily_logs
from logbook.copying import copy_discovered_recordings
from logbook.ledger import open_ledger
from logbook.routing import route_transcripts
from logbook.transcription import transcribe_copied_with_fake_odin


class ConsolidationTests(TestCase):
    def test_consolidates_routed_logs_to_canonical_daily_log(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            _write_recording(app_config.recorder.recordings_dir / "260428_0810.mp3", 8, 10)
            _write_recording(app_config.recorder.recordings_dir / "260428_1222.mp3", 12, 22)
            copy_discovered_recordings(app_config)
            transcribe_copied_with_fake_odin(app_config)
            vault_root = root / "test-vault"
            route_transcripts(app_config, vault_root)

            result = consolidate_daily_logs(app_config, vault_root)

            self.assertEqual(result.consolidated_count, 1)
            output_path = result.items[0].daily_log_path
            self.assertIsNotNone(output_path)
            self.assertEqual(
                output_path.relative_to(vault_root),
                Path("06 - Timestamps/2026/04-April/2026-04-28-Tuesday-Log.md"),
            )
            rendered = output_path.read_text(encoding="utf-8")
            self.assertIn('type: "daily_log"', rendered)
            self.assertIn('entry_count: "2"', rendered)
            self.assertIn("# Tuesday, April 28, 2026 Log", rendered)
            self.assertLess(rendered.index("## 08:10"), rendered.index("## 12:22"))
            self.assertIn("_Source: `logbook-job-1`_", rendered)
            self.assertNotIn(".mp3", rendered)

            ledger = open_ledger(app_config.sqlite_path)
            try:
                jobs = ledger.log_jobs_for_consolidation()
                first = ledger.get_by_id(1)
            finally:
                ledger.close()

            self.assertEqual(jobs, [])
            self.assertIsNotNone(first)
            self.assertEqual(first.status, "consolidated")
            self.assertEqual(
                first.daily_log_path,
                "06 - Timestamps/2026/04-April/2026-04-28-Tuesday-Log.md",
            )

    def test_consolidation_can_limit_to_one_date(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            _write_recording(app_config.recorder.recordings_dir / "260427_0740.mp3", 7, 40)
            _write_recording(app_config.recorder.recordings_dir / "260428_0810.mp3", 8, 10)
            copy_discovered_recordings(app_config)
            transcribe_copied_with_fake_odin(app_config)
            vault_root = root / "test-vault"
            route_transcripts(app_config, vault_root)

            result = consolidate_daily_logs(app_config, vault_root, entry_date="2026-04-27")

            self.assertEqual(result.consolidated_count, 1)
            self.assertEqual(result.items[0].entry_date, "2026-04-27")
            self.assertFalse(
                (vault_root / "06 - Timestamps/2026/04-April/2026-04-28-Tuesday-Log.md").exists()
            )


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


def _write_recording(path: Path, hour: int, minute: int) -> None:
    path.write_bytes(f"fake mp3 bytes {path.name}".encode("utf-8"))
    timestamp = datetime(2026, 4, int(path.name[4:6]), hour, minute, 54).timestamp()
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
