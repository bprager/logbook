from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from logbook.config import AppConfig, OdinConfig, RecorderConfig
from logbook.consolidation import consolidate_daily_logs
from logbook.copying import copy_discovered_recordings
from logbook.preview import write_open_log_preview
from logbook.transcription import transcribe_copied_with_fake_odin
from logbook.routing import route_transcripts


class OpenLogPreviewTests(TestCase):
    def test_writes_non_canonical_preview_for_consolidated_current_day(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            _write_recording(app_config.recorder.recordings_dir / "260429_0821.mp3", 8, 21)
            _write_recording(app_config.recorder.recordings_dir / "260429_0900.mp3", 9, 0)
            copy_discovered_recordings(app_config)
            transcribe_copied_with_fake_odin(app_config)
            vault_root = root / "test-vault"
            route_transcripts(app_config, vault_root)
            consolidate_daily_logs(app_config, vault_root, entry_date="2026-04-29")

            result = write_open_log_preview(
                app_config,
                vault_root,
                entry_date="2026-04-29",
            )

            self.assertEqual(result.status, "preview_written")
            self.assertEqual(result.entry_count, 2)
            self.assertEqual(
                result.preview_path.relative_to(vault_root),
                Path("10 - Logs/00 - Inbox/Open-Log-Preview.md"),
            )
            rendered = result.preview_path.read_text(encoding="utf-8")
            self.assertIn('type: "open_log_preview"', rendered)
            self.assertIn('canonical: "false"', rendered)
            self.assertIn("generated, non-canonical preview", rendered)
            self.assertIn(
                "06 - Timestamps/2026/04-April/2026-04-29-Wednesday-Log.md",
                rendered,
            )
            self.assertLess(rendered.index("## 08:21"), rendered.index("## 09:00"))
            self.assertIn("_Source: `logbook-job-1`_", rendered)
            self.assertNotIn(".mp3", rendered)
            self.assertEqual(
                len(
                    list(
                        (vault_root / "06 - Timestamps/2026/04-April").glob(
                            "2026-04-29*Log*.md"
                        )
                    )
                ),
                1,
            )

    def test_preview_can_render_empty_date_without_canonical_log(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            vault_root = root / "test-vault"

            result = write_open_log_preview(
                app_config,
                vault_root,
                entry_date="2026-04-30",
            )

            self.assertEqual(result.status, "preview_written")
            self.assertEqual(result.entry_count, 0)
            rendered = result.preview_path.read_text(encoding="utf-8")
            self.assertIn('entry_count: "0"', rendered)
            self.assertIn("No log entries are currently staged for this date.", rendered)
            self.assertFalse((vault_root / "06 - Timestamps").exists())


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
