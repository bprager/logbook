from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from logbook.config import AppConfig, OdinConfig, RecorderConfig
from logbook.consolidation import consolidate_daily_logs
from logbook.copying import copy_discovered_recordings
from logbook.dead_letters import (
    assign_dead_letter_to_log,
    rescue_dead_letter_as_meeting,
    discard_dead_letter,
    list_dead_letters,
)
from logbook.ledger import open_ledger
from logbook.odin import FakeOdinClient
from logbook.routing import route_transcripts
from logbook.transcription import transcribe_copied_with_fake_odin


class DeadLetterManagementTests(TestCase):
    def test_assign_dead_letter_to_log_rebuilds_daily_log_and_links_entities(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            vault_root = root / "test-vault"
            _write_recording(app_config.recorder.recordings_dir / "260430_0800.mp3", 8, 0)
            _write_recording(app_config.recorder.recordings_dir / "260430_0900.mp3", 9, 0)
            copy_discovered_recordings(app_config)
            transcribed = transcribe_copied_with_fake_odin(app_config)
            _set_transcript_text(transcribed.items[0].transcript_path, "Log entry first entry.")
            _set_transcript_text(
                transcribed.items[1].transcript_path,
                "Quinn needs his homework folder.",
            )
            route_transcripts(app_config, vault_root)
            consolidate_daily_logs(app_config, vault_root, entry_date="2026-04-30")
            _write(vault_root / "04 - People" / "Quinn Wolf Prager.md", "# Quinn\n")
            dead_letter = _pending_dead_letter(app_config)
            original_dead_letter_path = vault_root / dead_letter.obsidian_path

            result = assign_dead_letter_to_log(
                config=app_config,
                vault_root=vault_root,
                job_id=dead_letter.id,
                execute=True,
                requested_by="test",
                reason="spoken log prefix was clipped",
                linker_months=120,
                today=date(2026, 5, 1),
            )

            self.assertEqual(result.status, "assigned")
            self.assertIsNotNone(result.inbox_path)
            self.assertTrue(result.inbox_path.exists())
            self.assertEqual(result.removed_dead_letter_path, original_dead_letter_path)
            self.assertFalse(original_dead_letter_path.exists())
            self.assertIsNotNone(result.daily_log_path)
            rendered = result.daily_log_path.read_text(encoding="utf-8")
            self.assertIn("first entry", rendered)
            self.assertIn("[[04 - People/Quinn Wolf Prager|Quinn]] needs", rendered)
            self.assertLess(rendered.index("## 08:00"), rendered.index("## 09:00"))
            self.assertEqual(result.consolidation.consolidated_count, 1)
            self.assertEqual(result.entity_links.inserted_count, 1)

            ledger = open_ledger(app_config.sqlite_path)
            try:
                rescued = ledger.get_by_id(dead_letter.id)
                audit_rows = ledger.connection.execute(
                    "SELECT action_type, request_payload FROM action_audit"
                ).fetchall()
            finally:
                ledger.close()

            self.assertIsNotNone(rescued)
            self.assertEqual(rescued.status, "consolidated")
            self.assertEqual(rescued.classification, "log")
            self.assertIsNone(rescued.vault_synced_at)
            self.assertEqual(audit_rows[0]["action_type"], "dead_letter.assign")
            self.assertIn("spoken log prefix", audit_rows[0]["request_payload"])
            self.assertIn("removed_dead_letter_path", audit_rows[0]["request_payload"])

    def test_assign_dead_letter_dry_run_does_not_mutate_ledger_or_vault(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            vault_root = root / "test-vault"
            dead_letter = _seed_dead_letter(app_config, vault_root)

            result = assign_dead_letter_to_log(
                config=app_config,
                vault_root=vault_root,
                job_id=dead_letter.id,
                execute=False,
            )

            self.assertEqual(result.status, "would_assign")
            self.assertFalse(result.inbox_path.exists())
            ledger = open_ledger(app_config.sqlite_path)
            try:
                unchanged = ledger.get_by_id(dead_letter.id)
                audit_count = ledger.connection.execute(
                    "SELECT COUNT(*) AS count FROM action_audit"
                ).fetchone()["count"]
            finally:
                ledger.close()
            self.assertEqual(unchanged.status, "dead_letter_written")
            self.assertEqual(audit_count, 0)

    def test_rescue_dead_letter_as_meeting_dry_run_does_not_mutate_or_delete(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            vault_root = root / "test-vault"
            dead_letter = _seed_dead_letter(app_config, vault_root)
            dead_letter_path = vault_root / dead_letter.obsidian_path

            result = rescue_dead_letter_as_meeting(
                config=app_config,
                vault_root=vault_root,
                job_id=dead_letter.id,
                execute=False,
                client=FakeOdinClient(app_config.odin),
            )

            self.assertEqual(result.status, "would_rescue")
            self.assertEqual(result.target_route_kind, "meeting")
            self.assertTrue(dead_letter_path.exists())
            self.assertIsNotNone(result.meeting_path)
            self.assertFalse(result.meeting_path.exists())
            ledger = open_ledger(app_config.sqlite_path)
            try:
                unchanged = ledger.get_by_id(dead_letter.id)
                audit_count = ledger.connection.execute(
                    "SELECT COUNT(*) AS count FROM action_audit"
                ).fetchone()["count"]
            finally:
                ledger.close()
            self.assertEqual(unchanged.status, "dead_letter_written")
            self.assertEqual(unchanged.classification, "dead_letter")
            self.assertEqual(audit_count, 0)

    def test_rescue_dead_letter_as_meeting_diarizes_routes_and_removes_dead_letter_note(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            vault_root = root / "test-vault"
            dead_letter = _seed_dead_letter(app_config, vault_root)
            dead_letter_path = vault_root / dead_letter.obsidian_path
            self.assertTrue(dead_letter_path.exists())

            result = rescue_dead_letter_as_meeting(
                config=app_config,
                vault_root=vault_root,
                job_id=dead_letter.id,
                execute=True,
                requested_by="test",
                reason="actually a meeting",
                client=FakeOdinClient(app_config.odin),
            )

            self.assertEqual(result.status, "rescued")
            self.assertEqual(result.target_route_kind, "meeting")
            self.assertIsNotNone(result.meeting_path)
            self.assertTrue(result.meeting_path.exists())
            self.assertFalse(dead_letter_path.exists())
            rendered = result.meeting_path.read_text(encoding="utf-8")
            self.assertIn('type: "meeting"', rendered)
            self.assertIn("## Transcript", rendered)

            ledger = open_ledger(app_config.sqlite_path)
            try:
                rescued = ledger.get_by_id(dead_letter.id)
                row = ledger.connection.execute(
                    "SELECT action_type, request_payload FROM action_audit"
                ).fetchone()
            finally:
                ledger.close()

            self.assertIsNotNone(rescued)
            self.assertEqual(rescued.status, "meeting_written")
            self.assertEqual(rescued.classification, "meeting")
            self.assertIsNotNone(rescued.diarization_path)
            self.assertEqual(
                rescued.obsidian_path,
                str(result.meeting_path.relative_to(vault_root)),
            )
            self.assertIsNone(rescued.vault_synced_at)
            self.assertEqual(row["action_type"], "dead_letter.rescue")
            self.assertIn("actually a meeting", row["request_payload"])

    def test_discard_dead_letter_records_audit_and_removes_from_pending_list(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            vault_root = root / "test-vault"
            dead_letter = _seed_dead_letter(app_config, vault_root)

            dry_run = discard_dead_letter(
                config=app_config,
                job_id=dead_letter.id,
                execute=False,
            )
            self.assertEqual(dry_run.status, "would_discard")
            self.assertEqual(len(list_dead_letters(app_config).items), 1)

            result = discard_dead_letter(
                config=app_config,
                job_id=dead_letter.id,
                execute=True,
                requested_by="test",
                reason="not actionable",
            )

            self.assertEqual(result.status, "discarded")
            self.assertEqual(len(list_dead_letters(app_config).items), 0)
            self.assertEqual(result.job.status, "dead_letter_discarded")
            ledger = open_ledger(app_config.sqlite_path)
            try:
                row = ledger.connection.execute(
                    "SELECT action_type, request_payload FROM action_audit"
                ).fetchone()
            finally:
                ledger.close()
            self.assertEqual(row["action_type"], "dead_letter.discard")
            self.assertIn("not actionable", row["request_payload"])


def _seed_dead_letter(app_config: AppConfig, vault_root: Path):
    _write_recording(app_config.recorder.recordings_dir / "260430_0900.mp3", 9, 0)
    copy_discovered_recordings(app_config)
    transcribed = transcribe_copied_with_fake_odin(app_config)
    _set_transcript_text(transcribed.items[0].transcript_path, "Unprefixed dead letter text.")
    route_transcripts(app_config, vault_root)
    return _pending_dead_letter(app_config)


def _pending_dead_letter(app_config: AppConfig):
    result = list_dead_letters(app_config)
    self_check = result.items[0].job if result.items else None
    if self_check is None:
        raise AssertionError("expected a pending dead letter")
    return self_check


def _set_transcript_text(path: Path | None, text: str) -> None:
    if path is None:
        raise AssertionError("expected transcript path")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["text"] = text
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_recording(path: Path, hour: int, minute: int) -> None:
    path.write_bytes(f"fake mp3 bytes {path.name}".encode("utf-8"))
    timestamp = datetime(2026, 4, int(path.name[4:6]), hour, minute, 54).timestamp()
    os.utime(path, (timestamp, timestamp))


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
        odin=OdinConfig(
            api_base_url="http://odin.test",
            api_token=None,
            asr_model="large-v3",
            asr_device="cuda",
            asr_compute_type="float16",
            asr_vad_filter=True,
            diarization_model="pyannote/speaker-diarization-3.1",
        ),
    )
