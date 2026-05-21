from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from logbook.cli import _process_mounted_recorder
from logbook.config import load_app_config
from logbook.copying import CopyResult, copy_discovered_recordings
from logbook.ledger import open_ledger
from logbook.odin import FakeOdinClient
from logbook.recorder import RecorderValidation
from logbook.routing import route_transcripts
from logbook.transcription import transcribe_copied_with_fake_odin
from logbook.writers import FilesystemNoteWriter


class _FilesystemWriterFactory:
    def __init__(self, *args, **kwargs) -> None:
        self._writer = FilesystemNoteWriter()

    def write_note(self, path: Path, content: str) -> None:
        self._writer.write_note(path, content)


class _TransientOdinClient(FakeOdinClient):
    submit_attempts = 0

    def submit_transcription(self, submit_request):
        type(self).submit_attempts += 1
        if type(self).submit_attempts == 1:
            raise OSError("No route to host")
        return super().submit_transcription(submit_request)


class ProcessMountedRecorderTests(TestCase):
    def test_process_mounted_recorder_consolidates_routed_log_entries(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            recordings_dir = root / "IC RECORDER" / "REC_FILE" / "FOLDER01"
            recordings_dir.mkdir(parents=True)
            _write_recording(recordings_dir / "260505_0808.mp3", 8, 8)
            _write_recording(recordings_dir / "260505_1501.mp3", 15, 1)

            with (
                patch("logbook.cli.HttpOdinClient", FakeOdinClient),
                patch("logbook.cli.ObsidianCliNoteWriter", _FilesystemWriterFactory),
                patch("logbook.cli._mark_vault_synced_and_sync_memory", return_value=True),
            ):
                exit_code = _process_mounted_recorder(env_path)

            self.assertEqual(exit_code, 0)
            daily_log = (
                root
                / "vault"
                / "06 - Timestamps"
                / "2026"
                / "05-May"
                / "2026-05-05-Tuesday-Log.md"
            )
            self.assertTrue(daily_log.exists())
            rendered = daily_log.read_text(encoding="utf-8")
            self.assertIn('entry_count: "2"', rendered)
            self.assertIn("_Source: `logbook-job-1`_", rendered)
            self.assertIn("_Source: `logbook-job-2`_", rendered)

            ledger = open_ledger(root / "VoiceIngest" / "voice_ingest.sqlite")
            try:
                jobs = [ledger.get_by_id(1), ledger.get_by_id(2)]
            finally:
                ledger.close()

            self.assertEqual([job.status for job in jobs if job is not None], ["consolidated", "consolidated"])

            ledger = open_ledger(root / "VoiceIngest" / "voice_ingest.sqlite")
            try:
                run = ledger.connection.execute(
                    "SELECT command, status, exit_code FROM pipeline_runs"
                ).fetchone()
                stages = [
                    row["stage"]
                    for row in ledger.connection.execute(
                        """
                        SELECT stage
                        FROM pipeline_stage_events
                        WHERE event = 'succeeded'
                        ORDER BY id
                        """
                    ).fetchall()
                ]
                progress_events = [
                    (row["stage"], row["progress_current"], row["progress_total"], row["progress_kind"])
                    for row in ledger.connection.execute(
                        """
                        SELECT stage, progress_current, progress_total, progress_kind
                        FROM pipeline_stage_events
                        WHERE event = 'progress'
                        ORDER BY id
                        """
                    ).fetchall()
                ]
            finally:
                ledger.close()

            self.assertEqual(run["command"], "process-mounted-recorder")
            self.assertEqual(run["status"], "succeeded")
            self.assertEqual(run["exit_code"], 0)
            self.assertEqual(stages, ["copy", "transcribe", "diarize", "route", "consolidate", "vault_sync"])
            self.assertIn(("route", 2.0, 2.0, "measured"), progress_events)
            self.assertIn(("consolidate", 1.0, 1.0, "measured"), progress_events)
            self.assertIn(("vault_sync", 1.0, 1.0, "measured"), progress_events)

    def test_process_mounted_recorder_finishes_local_work_when_recorder_copy_fails(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            recordings_dir = root / "IC RECORDER" / "REC_FILE" / "FOLDER01"
            recordings_dir.mkdir(parents=True)
            _write_recording(recordings_dir / "260505_0808.mp3", 8, 8)
            config = load_app_config(env_path)
            copy_discovered_recordings(config)
            transcribe_copied_with_fake_odin(config)
            route_transcripts(config, root / "vault")

            failed_copy = CopyResult(
                validation=RecorderValidation(
                    configured_mount_path=root / "IC RECORDER",
                    resolved_mount_path=root / "IC RECORDER",
                    recordings_dir=recordings_dir,
                    volume_name="IC RECORDER",
                    expected_volume_name="IC RECORDER",
                    exists=True,
                    readable=True,
                    writable=True,
                    warnings=(),
                ),
                inbox_dir=root / "VoiceIngest" / "inbox",
                ledger_path=root / "VoiceIngest" / "voice_ingest.sqlite",
                items=(),
                discovery_error="cannot read recordings directory: denied",
            )

            with (
                patch("logbook.cli.copy_discovered_recordings_with_retries", return_value=failed_copy),
                patch("logbook.cli.HttpOdinClient", FakeOdinClient),
                patch("logbook.cli.ObsidianCliNoteWriter", _FilesystemWriterFactory),
                patch("logbook.cli._mark_vault_synced_and_sync_memory", return_value=True),
            ):
                exit_code = _process_mounted_recorder(env_path)

            self.assertEqual(exit_code, 1)
            daily_log = (
                root
                / "vault"
                / "06 - Timestamps"
                / "2026"
                / "05-May"
                / "2026-05-05-Tuesday-Log.md"
            )
            self.assertTrue(daily_log.exists())

    def test_process_mounted_recorder_retries_transient_odin_failure(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            recordings_dir = root / "IC RECORDER" / "REC_FILE" / "FOLDER01"
            recordings_dir.mkdir(parents=True)
            _write_recording(recordings_dir / "260505_0808.mp3", 8, 8)
            _TransientOdinClient.submit_attempts = 0

            with (
                patch("logbook.cli.HttpOdinClient", _TransientOdinClient),
                patch("logbook.cli.ObsidianCliNoteWriter", _FilesystemWriterFactory),
                patch("logbook.cli._mark_vault_synced_and_sync_memory", return_value=True),
                patch("logbook.cli.time.sleep") as sleep,
            ):
                exit_code = _process_mounted_recorder(env_path)

            self.assertEqual(exit_code, 0)
            self.assertEqual(_TransientOdinClient.submit_attempts, 2)
            sleep.assert_called()

            ledger = open_ledger(root / "VoiceIngest" / "voice_ingest.sqlite")
            try:
                job = ledger.get_by_id(1)
            finally:
                ledger.close()

            self.assertIsNotNone(job)
            self.assertEqual(job.status, "consolidated")

    def test_process_mounted_recorder_skips_vault_sync_when_no_new_work(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            recordings_dir = root / "IC RECORDER" / "REC_FILE" / "FOLDER01"
            recordings_dir.mkdir(parents=True)

            with (
                patch("logbook.cli.HttpOdinClient", FakeOdinClient),
                patch(
                    "logbook.cli._mark_vault_synced_and_sync_memory",
                    side_effect=AssertionError("vault sync should be skipped"),
                ),
            ):
                exit_code = _process_mounted_recorder(env_path)

            self.assertEqual(exit_code, 0)


def _write_env(root: Path) -> Path:
    env_path = root / ".env"
    env_path.write_text(
        "\n".join(
            [
                f"LOGBOOK_PROCESSING_ROOT={root / 'VoiceIngest'}",
                "SONY_RECORDER_VOLUME_NAME=IC RECORDER",
                f"SONY_RECORDER_MOUNT_PATH={root / 'IC RECORDER'}",
                "SONY_RECORDER_RECORDINGS_PATH=/REC_FILE/FOLDER01",
                "ODIN_API_BASE_URL=http://odin.test",
                "OBSIDIAN_CLI_BIN=" + sys.executable,
                f"OBSIDIAN_VAULT_LOCAL_PATH={root / 'vault'}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "vault").mkdir()
    return env_path


def _write_recording(path: Path, hour: int, minute: int) -> None:
    path.write_bytes(f"fake mp3 bytes {path.name}".encode("utf-8"))
    timestamp = datetime(2026, 5, 5, hour, minute, 10).timestamp()
    os.utime(path, (timestamp, timestamp))
