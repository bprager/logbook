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
from logbook.insights import extract_insights
from logbook.ledger import open_ledger
from logbook.routing import route_transcripts
from logbook.transcription import transcribe_copied_with_fake_odin


class InsightExtractionTests(TestCase):
    def test_extracts_task_summary_and_actions_to_review_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            _write_recording(app_config.recorder.recordings_dir / "260429_1000.mp3", 10, 0)
            copy_discovered_recordings(app_config)
            transcribe_copied_with_fake_odin(app_config)
            _rewrite_transcript(
                app_config.sqlite_path,
                1,
                "task Follow up with finance. Remember to send the budget.",
            )
            vault_root = root / "test-vault"
            route_transcripts(app_config, vault_root)
            source_note = _job(app_config.sqlite_path, 1).obsidian_path
            self.assertIsNotNone(source_note)
            source_note_path = vault_root / source_note
            source_before = source_note_path.read_text(encoding="utf-8")

            result = extract_insights(app_config, vault_root, job_id=1)

            self.assertEqual(result.extracted_count, 1)
            item = result.items[0]
            self.assertEqual(item.status, "insight_review_written")
            self.assertEqual(
                tuple(item.action_items),
                ("Follow up with finance.", "Remember to send the budget."),
            )
            self.assertIsNotNone(item.artifact_path)
            artifact = json.loads(item.artifact_path.read_text(encoding="utf-8"))
            self.assertEqual(artifact["review_status"], "needs_review")
            self.assertIs(artifact["canonical"], False)
            self.assertEqual(artifact["category"], "task")
            self.assertEqual(artifact["summary"], "Follow up with finance. Remember to send the budget.")
            self.assertEqual(
                artifact["action_items"],
                ["Follow up with finance.", "Remember to send the budget."],
            )
            self.assertIsNotNone(item.review_note_path)
            review = item.review_note_path.read_text(encoding="utf-8")
            self.assertIn('type: "logbook_insight_review"', review)
            self.assertIn('canonical: "false"', review)
            self.assertIn("- [ ] Follow up with finance.", review)
            self.assertIn("does not update the source note", review)
            self.assertEqual(source_note_path.read_text(encoding="utf-8"), source_before)

    def test_extracts_meeting_action_candidates_from_diarized_segments(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            _write_recording(app_config.recorder.recordings_dir / "260429_1100.mp3", 11, 0)
            copy_discovered_recordings(app_config)
            transcribe_copied_with_fake_odin(app_config)
            _rewrite_transcript(
                app_config.sqlite_path,
                1,
                "meeting Project sync. Action item follow up with Alex.",
            )
            diarize_meetings_with_fake_odin(app_config)
            _rewrite_diarization(
                app_config.sqlite_path,
                1,
                [
                    {
                        "start_seconds": 0.0,
                        "end_seconds": 2.0,
                        "speaker": "SPEAKER_00",
                        "text": "meeting Project sync.",
                    },
                    {
                        "start_seconds": 2.0,
                        "end_seconds": 4.0,
                        "speaker": "SPEAKER_01",
                        "text": "Action item follow up with Alex.",
                    },
                ],
            )
            vault_root = root / "test-vault"
            route_transcripts(app_config, vault_root)

            result = extract_insights(app_config, vault_root, job_id=1)

            self.assertEqual(result.extracted_count, 1)
            item = result.items[0]
            self.assertEqual(tuple(item.action_items), ("Action item follow up with Alex.",))
            self.assertIsNotNone(item.review_note_path)
            review = item.review_note_path.read_text(encoding="utf-8")
            self.assertIn('route_kind: "meeting"', review)
            self.assertIn("- [ ] Action item follow up with Alex.", review)
            self.assertNotIn(".mp3", review)


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


def _rewrite_transcript(sqlite_path: Path, job_id: int, text: str) -> None:
    job = _job(sqlite_path, job_id)
    assert job.transcript_path is not None
    path = Path(job.transcript_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["text"] = text
    payload["segments"] = [
        {
            "start_seconds": 0.0,
            "end_seconds": 2.0,
            "speaker": None,
            "text": text,
        }
    ]
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _rewrite_diarization(sqlite_path: Path, job_id: int, segments: list[dict]) -> None:
    job = _job(sqlite_path, job_id)
    assert job.diarization_path is not None
    path = Path(job.diarization_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["text"] = " ".join(str(segment["text"]) for segment in segments)
    payload["segments"] = segments
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _job(sqlite_path: Path, job_id: int):
    ledger = open_ledger(sqlite_path)
    try:
        job = ledger.get_by_id(job_id)
    finally:
        ledger.close()
    assert job is not None
    return job


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
