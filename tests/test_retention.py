from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from logbook.config import AppConfig, OdinConfig, RecorderConfig, RetentionConfig
from logbook.copying import copy_discovered_recordings
from logbook.ledger import open_ledger
from logbook.retention import execute_audio_cleanup, plan_audio_cleanup
from logbook.routing import route_transcripts
from logbook.transcription import transcribe_copied_with_fake_odin


class RetentionTests(TestCase):
    def test_plan_blocks_until_vault_sync_and_retention_window(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            job = _route_category_job(app_config)
            now = datetime.now(timezone.utc)

            missing_sync = plan_audio_cleanup(app_config, now=now + timedelta(hours=25))
            self.assertEqual(missing_sync.eligible_count, 0)
            self.assertIn("missing_vault_sync", missing_sync.items[0].blockers)

            ledger = open_ledger(app_config.sqlite_path, initialize=True)
            try:
                ledger.mark_vault_synced(job.checksum_sha256, now.isoformat(timespec="seconds"))
            finally:
                ledger.close()

            too_early = plan_audio_cleanup(app_config, now=now + timedelta(hours=23))
            self.assertEqual(too_early.eligible_count, 0)
            self.assertIn("retention_window_open", too_early.items[0].blockers)

            eligible = plan_audio_cleanup(app_config, now=now + timedelta(hours=25))
            self.assertEqual(eligible.eligible_count, 1)
            self.assertEqual(eligible.items[0].local_action, "trash")
            self.assertEqual(eligible.items[0].recorder_action, "delete")
            ledger = open_ledger(app_config.sqlite_path)
            try:
                planned_job = ledger.get_by_checksum(job.checksum_sha256)
            finally:
                ledger.close()

            self.assertIsNotNone(planned_job)
            self.assertIsNone(planned_job.cleanup_eligible_at)

    def test_execute_trashes_local_audio_without_recorder_flag(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            job = _route_category_job(app_config)
            now = datetime.now(timezone.utc)
            copied_path = Path(job.copied_path or "")
            source_path = Path(job.source_path)
            ledger = open_ledger(app_config.sqlite_path, initialize=True)
            try:
                ledger.mark_vault_synced(job.checksum_sha256, now.isoformat(timespec="seconds"))
            finally:
                ledger.close()

            result = execute_audio_cleanup(app_config, now=now + timedelta(hours=25))

            self.assertEqual(result.local_pending_count, 0)
            self.assertFalse(copied_path.exists())
            self.assertTrue(source_path.exists())
            trashed = list((app_config.processing_root / "trash" / "local-audio").glob("*.mp3"))
            self.assertEqual(len(trashed), 1)

            ledger = open_ledger(app_config.sqlite_path)
            try:
                updated = ledger.get_by_checksum(job.checksum_sha256)
            finally:
                ledger.close()

            self.assertIsNotNone(updated)
            self.assertIsNotNone(updated.cleanup_eligible_at)
            self.assertEqual(updated.local_audio_cleanup_status, "trashed")
            self.assertIsNone(updated.recorder_audio_cleanup_status)
            self.assertEqual(updated.cleanup_attempt_count, 1)

    def test_execute_deletes_recorder_audio_only_when_enabled_and_checksum_matches(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root, cleanup_mode="delete")
            job = _route_category_job(app_config)
            now = datetime.now(timezone.utc)
            copied_path = Path(job.copied_path or "")
            source_path = Path(job.source_path)
            ledger = open_ledger(app_config.sqlite_path, initialize=True)
            try:
                ledger.mark_vault_synced(job.checksum_sha256, now.isoformat(timespec="seconds"))
            finally:
                ledger.close()

            execute_audio_cleanup(
                app_config,
                include_recorder=True,
                now=now + timedelta(hours=25),
            )

            self.assertFalse(copied_path.exists())
            self.assertFalse(source_path.exists())
            ledger = open_ledger(app_config.sqlite_path)
            try:
                updated = ledger.get_by_checksum(job.checksum_sha256)
            finally:
                ledger.close()

            self.assertIsNotNone(updated)
            self.assertEqual(updated.local_audio_cleanup_status, "deleted")
            self.assertEqual(updated.recorder_audio_cleanup_status, "deleted")
            self.assertEqual(updated.cleanup_attempt_count, 1)

    def test_checksum_mismatch_keeps_files_and_records_failure(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root, cleanup_mode="delete")
            job = _route_category_job(app_config)
            now = datetime.now(timezone.utc)
            source_path = Path(job.source_path)
            source_path.write_bytes(b"changed after ingest")
            ledger = open_ledger(app_config.sqlite_path, initialize=True)
            try:
                ledger.mark_vault_synced(job.checksum_sha256, now.isoformat(timespec="seconds"))
            finally:
                ledger.close()

            execute_audio_cleanup(
                app_config,
                include_recorder=True,
                now=now + timedelta(hours=25),
            )

            self.assertTrue(source_path.exists())
            ledger = open_ledger(app_config.sqlite_path)
            try:
                updated = ledger.get_by_checksum(job.checksum_sha256)
            finally:
                ledger.close()

            self.assertIsNotNone(updated)
            self.assertEqual(updated.recorder_audio_cleanup_status, "failed")
            self.assertIn("checksum mismatch", updated.cleanup_last_error or "")


def _route_category_job(app_config: AppConfig):
    _write_recording(app_config.recorder.recordings_dir / "260429_0821.mp3")
    copy_discovered_recordings(app_config)
    transcribe_result = transcribe_copied_with_fake_odin(app_config)
    transcript_path = transcribe_result.items[0].transcript_path
    assert transcript_path is not None
    payload = json.loads(transcript_path.read_text(encoding="utf-8"))
    payload["text"] = "Task clean this up later."
    transcript_path.write_text(json.dumps(payload), encoding="utf-8")
    route_result = route_transcripts(app_config, app_config.processing_root / "test-vault")
    return route_result.items[0].job


def _app_config(root: Path, cleanup_mode: str = "trash_then_delete") -> AppConfig:
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
        retention=RetentionConfig(hours=24, cleanup_mode=cleanup_mode),
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
