from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from logbook.config import AppConfig, OdinConfig, RecorderConfig
from logbook.copying import copy_discovered_recordings, copy_discovered_recordings_with_retries
from logbook.ledger import open_ledger
from logbook.recorder import RecorderAccessError


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

    def test_copy_discovered_recordings_reports_measured_byte_progress(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            first = app_config.recorder.recordings_dir / "260429_0821.mp3"
            second = app_config.recorder.recordings_dir / "260429_0822.mp3"
            _write_recording(first, b"a" * 10)
            _write_recording(second, b"b" * 6)
            progress: list[tuple[int, int]] = []

            result = copy_discovered_recordings(
                app_config,
                progress_callback=lambda current, total: progress.append((current, total)),
            )

            self.assertEqual(result.copied_count, 2)
            self.assertGreaterEqual(len(progress), 2)
            self.assertEqual(progress[-1], (16, 16))
            self.assertTrue(all(current <= total for current, total in progress))
            self.assertTrue(all(total == 16 for _, total in progress))

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

    def test_copy_does_not_downgrade_consolidated_job_on_rediscovery(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            _write_recording(app_config.recorder.recordings_dir / "260429_0821.mp3", b"audio")

            first = copy_discovered_recordings(app_config)
            ledger = open_ledger(app_config.sqlite_path)
            try:
                ledger.mark_consolidated(
                    first.items[0].checksum_sha256,
                    Path("06 - Timestamps/2026/04-April/2026-04-29-Wednesday-Log.md"),
                )
            finally:
                ledger.close()

            second = copy_discovered_recordings(app_config)

            self.assertEqual(second.copied_count, 0)
            self.assertEqual(second.skipped_count, 1)
            ledger = open_ledger(app_config.sqlite_path)
            try:
                job = ledger.get_by_checksum(first.items[0].checksum_sha256)
            finally:
                ledger.close()

            self.assertIsNotNone(job)
            self.assertEqual(job.status, "consolidated")

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

    def test_copy_does_not_preserve_recorder_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            source = app_config.recorder.recordings_dir / "260429_0821.mp3"
            _write_recording(source, b"audio")

            result = copy_discovered_recordings(app_config)

            self.assertEqual(result.copied_count, 1)
            self.assertEqual(result.failed_count, 0)
            self.assertEqual(result.items[0].copied_path.read_bytes(), b"audio")

    def test_copy_reports_recorder_access_error_without_crashing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)

            with patch(
                "logbook.copying.discover_recordings",
                side_effect=RecorderAccessError("cannot read recordings directory: denied"),
            ):
                result = copy_discovered_recordings(app_config)

            self.assertEqual(result.copied_count, 0)
            self.assertEqual(result.failed_count, 1)
            self.assertEqual(result.discovery_error, "cannot read recordings directory: denied")

    def test_copy_retry_recovers_after_transient_recorder_access_error(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            _write_recording(app_config.recorder.recordings_dir / "260429_0821.mp3", b"audio")
            attempts = 0

            def flaky_discovery(recordings_dir: Path):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise RecorderAccessError("cannot read recordings directory: denied")
                from logbook.recorder import discover_recordings

                return discover_recordings(recordings_dir)

            with patch("logbook.copying.discover_recordings", side_effect=flaky_discovery):
                result = copy_discovered_recordings_with_retries(
                    app_config,
                    attempts=2,
                    delay_seconds=0,
                )

            self.assertEqual(attempts, 2)
            self.assertEqual(result.discovery_error, None)
            self.assertEqual(result.copied_count, 1)
            self.assertEqual(result.failed_count, 0)

    def test_copy_retry_default_covers_slow_mount_access(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            attempts = 0

            def unavailable(recordings_dir: Path):
                nonlocal attempts
                attempts += 1
                raise RecorderAccessError("cannot read recordings directory: denied")

            with patch("logbook.copying.discover_recordings", side_effect=unavailable):
                result = copy_discovered_recordings_with_retries(
                    app_config,
                    delay_seconds=0,
                )

            self.assertEqual(attempts, 24)
            self.assertEqual(result.attempt_count, 24)
            self.assertEqual(result.failed_count, 1)


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
