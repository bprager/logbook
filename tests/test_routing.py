from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from logbook.config import AppConfig, OdinConfig, RecorderConfig
from logbook.copying import copy_discovered_recordings
from logbook.diarization import diarize_meetings_with_fake_odin
from logbook.ledger import open_ledger
from logbook.routing import route_transcripts
from logbook.transcription import transcribe_copied_with_fake_odin


class RoutingTests(TestCase):
    def test_routes_fake_log_transcript_into_test_vault_inbox(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            _write_recording(app_config.recorder.recordings_dir / "260429_0821.mp3")
            copy_discovered_recordings(app_config)
            transcribe_copied_with_fake_odin(app_config)

            vault_root = root / "test-vault"
            result = route_transcripts(app_config, vault_root)

            self.assertEqual(result.routed_count, 1)
            self.assertEqual(result.log_count, 1)
            output_path = result.items[0].output_path
            self.assertIsNotNone(output_path)
            self.assertEqual(
                output_path.relative_to(vault_root),
                Path(
                    "10 - Logs/00 - Inbox/2026/04-April/2026-04-29/"
                    "2026-04-29T08-21-00-job-000001-log-entry.md"
                ),
            )
            rendered = output_path.read_text(encoding="utf-8")
            self.assertIn('type: "log"', rendered)
            self.assertIn('job_id: "1"', rendered)
            self.assertIn("# Log entry 2026-04-29 08:21", rendered)
            self.assertIn("Placeholder transcript", rendered)
            self.assertNotIn(".mp3", rendered)

            ledger = open_ledger(app_config.sqlite_path)
            try:
                job = ledger.get_by_checksum(result.items[0].job.checksum_sha256)
            finally:
                ledger.close()

            self.assertIsNotNone(job)
            self.assertEqual(job.status, "inbox_written")
            self.assertEqual(job.classification, "log")
            self.assertEqual(
                job.obsidian_path,
                str(output_path.relative_to(vault_root)),
            )

    def test_routes_unknown_transcript_to_dead_letter(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            _write_recording(app_config.recorder.recordings_dir / "260429_0821.mp3")
            copy_discovered_recordings(app_config)
            transcribe_result = transcribe_copied_with_fake_odin(app_config)
            transcript_path = transcribe_result.items[0].transcript_path
            self.assertIsNotNone(transcript_path)
            payload = json.loads(transcript_path.read_text(encoding="utf-8"))
            payload["text"] = "This has no useful prefix."
            transcript_path.write_text(json.dumps(payload), encoding="utf-8")

            vault_root = root / "test-vault"
            result = route_transcripts(app_config, vault_root)

            self.assertEqual(result.dead_letter_count, 1)
            output_path = result.items[0].output_path
            self.assertIsNotNone(output_path)
            self.assertEqual(
                output_path.relative_to(vault_root),
                Path("99 - Dead Letters/2026-04-29T08-21-00-job-000001.md"),
            )
            rendered = output_path.read_text(encoding="utf-8")
            self.assertIn('type: "dead_letter"', rendered)
            self.assertIn('review_status: "needs_review"', rendered)
            self.assertIn('delete_after: "2026-05-27"', rendered)
            self.assertIn("## Review", rendered)
            self.assertIn("This has no useful prefix.", rendered)
            self.assertNotIn(".mp3", rendered)

            ledger = open_ledger(app_config.sqlite_path)
            try:
                job = ledger.get_by_checksum(result.items[0].job.checksum_sha256)
            finally:
                ledger.close()

            self.assertIsNotNone(job)
            self.assertEqual(job.status, "dead_letter_written")
            self.assertEqual(job.classification, "dead_letter")
            self.assertEqual(job.obsidian_path, str(output_path.relative_to(vault_root)))

    def test_routes_category_transcript_to_category_note(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            _write_recording(app_config.recorder.recordings_dir / "260429_0821.mp3")
            copy_discovered_recordings(app_config)
            transcribe_result = transcribe_copied_with_fake_odin(app_config)
            transcript_path = transcribe_result.items[0].transcript_path
            self.assertIsNotNone(transcript_path)
            payload = json.loads(transcript_path.read_text(encoding="utf-8"))
            payload["text"] = "To do: add route telemetry later."
            transcript_path.write_text(json.dumps(payload), encoding="utf-8")

            vault_root = root / "test-vault"
            result = route_transcripts(app_config, vault_root)

            self.assertEqual(result.routed_count, 1)
            output_path = result.items[0].output_path
            self.assertIsNotNone(output_path)
            self.assertEqual(
                output_path.relative_to(vault_root),
                Path("20 - Notes/00 - Inbox/task/2026-04-29T08-21-00-job-000001-task.md"),
            )
            rendered = output_path.read_text(encoding="utf-8")
            self.assertIn('type: "category"', rendered)
            self.assertIn('category: "task"', rendered)
            self.assertIn("# Task note 2026-04-29 08:21", rendered)
            self.assertIn("add route telemetry later.", rendered)
            self.assertNotIn("To do:", rendered)
            self.assertNotIn(".mp3", rendered)

            ledger = open_ledger(app_config.sqlite_path)
            try:
                job = ledger.get_by_checksum(result.items[0].job.checksum_sha256)
            finally:
                ledger.close()

            self.assertIsNotNone(job)
            self.assertEqual(job.status, "category_written")
            self.assertEqual(job.classification, "category:task")
            self.assertEqual(job.obsidian_path, str(output_path.relative_to(vault_root)))

    def test_routes_diarized_meeting_to_meeting_note_with_speakers(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            _write_recording(app_config.recorder.recordings_dir / "260429_0821.mp3")
            copy_discovered_recordings(app_config)
            transcribe_result = transcribe_copied_with_fake_odin(app_config)
            transcript_path = transcribe_result.items[0].transcript_path
            self.assertIsNotNone(transcript_path)
            payload = json.loads(transcript_path.read_text(encoding="utf-8"))
            payload["text"] = "Meeting weekly planning."
            transcript_path.write_text(json.dumps(payload), encoding="utf-8")
            diarize_meetings_with_fake_odin(app_config)

            vault_root = root / "test-vault"
            result = route_transcripts(app_config, vault_root)

            self.assertEqual(result.routed_count, 1)
            self.assertEqual(result.items[0].status, "meeting_written")
            output_path = result.items[0].output_path
            self.assertIsNotNone(output_path)
            self.assertEqual(
                output_path.relative_to(vault_root),
                Path("30 - Meetings/2026/04-April/2026-04-29T08-21-00-job-000001-meeting.md"),
            )
            rendered = output_path.read_text(encoding="utf-8")
            self.assertIn('type: "meeting"', rendered)
            self.assertIn('speaker_count: "2"', rendered)
            self.assertIn('diarization_model: "pyannote/speaker-diarization-3.1"', rendered)
            self.assertIn("## Participants", rendered)
            self.assertIn("- SPEAKER_00: ", rendered)
            self.assertIn("- SPEAKER_01: ", rendered)
            self.assertIn("## Summary", rendered)
            self.assertIn("## Decisions", rendered)
            self.assertIn("## Action Items", rendered)
            self.assertIn("## Transcript", rendered)
            self.assertIn("**SPEAKER_00:** Placeholder", rendered)
            self.assertIn("**SPEAKER_01:** transcript.", rendered)
            self.assertNotIn("**SPEAKER_00:** meeting", rendered)
            self.assertNotIn(".mp3", rendered)

            ledger = open_ledger(app_config.sqlite_path)
            try:
                job = ledger.get_by_checksum(result.items[0].job.checksum_sha256)
            finally:
                ledger.close()

            self.assertIsNotNone(job)
            self.assertEqual(job.status, "meeting_written")
            self.assertEqual(job.classification, "meeting")
            self.assertEqual(job.obsidian_path, str(output_path.relative_to(vault_root)))

    def test_transcribed_meeting_without_diarization_is_not_written(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            _write_recording(app_config.recorder.recordings_dir / "260429_0821.mp3")
            copy_discovered_recordings(app_config)
            transcribe_result = transcribe_copied_with_fake_odin(app_config)
            transcript_path = transcribe_result.items[0].transcript_path
            self.assertIsNotNone(transcript_path)
            payload = json.loads(transcript_path.read_text(encoding="utf-8"))
            payload["text"] = "Meeting weekly planning."
            transcript_path.write_text(json.dumps(payload), encoding="utf-8")

            vault_root = root / "test-vault"
            result = route_transcripts(app_config, vault_root)

            self.assertEqual(result.routed_count, 0)
            self.assertEqual(result.failed_count, 1)
            self.assertEqual(result.items[0].status, "failed_missing_diarization")
            self.assertEqual(list(vault_root.rglob("*.md")), [])

            ledger = open_ledger(app_config.sqlite_path)
            try:
                job = ledger.get_by_checksum(transcribe_result.items[0].job.checksum_sha256)
            finally:
                ledger.close()

            self.assertIsNotNone(job)
            self.assertEqual(job.status, "transcribed")
            self.assertIsNone(job.obsidian_path)

    def test_job_id_routes_one_job_even_after_it_was_routed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            _write_recording(app_config.recorder.recordings_dir / "260429_0821.mp3")
            copy_discovered_recordings(app_config)
            transcribe_copied_with_fake_odin(app_config)

            vault_root = root / "test-vault"
            first = route_transcripts(app_config, vault_root)
            second = route_transcripts(app_config, vault_root, job_id=first.items[0].job.id)

            self.assertEqual(len(second.items), 1)
            self.assertEqual(second.routed_count, 1)
            self.assertEqual(second.items[0].status, "inbox_written")

    def test_include_routed_routes_jobs_that_were_already_routed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            _write_recording(app_config.recorder.recordings_dir / "260429_0821.mp3")
            copy_discovered_recordings(app_config)
            transcribe_copied_with_fake_odin(app_config)

            vault_root = root / "test-vault"
            route_transcripts(app_config, vault_root)
            normal = route_transcripts(app_config, vault_root)
            included = route_transcripts(app_config, vault_root, include_routed=True)

            self.assertEqual(normal.routed_count, 0)
            self.assertEqual(included.routed_count, 1)
            self.assertEqual(included.items[0].status, "inbox_written")


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
