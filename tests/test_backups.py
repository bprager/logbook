from __future__ import annotations

import os
import io
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from logbook.backup import backup_ssh_command, run_backup, run_restore_drill
from logbook.cli import main
from logbook.config import AppConfig, OdinConfig, RecorderConfig
from logbook.ledger import open_ledger
from logbook.recorder import discover_recordings


class BackupTests(TestCase):
    def test_backup_ssh_command_uses_explicit_identity_when_configured(self) -> None:
        command = backup_ssh_command("/Users/bernd/.ssh/id_rsa_odin")

        self.assertEqual(
            command,
            [
                "ssh",
                "-i",
                "/Users/bernd/.ssh/id_rsa_odin",
                "-o",
                "IdentitiesOnly=yes",
            ],
        )

    def test_backup_cli_is_dry_run_first_and_restore_drill_reports_counts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            app_config = _app_config(root)
            _seed_state(app_config, root)
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "backup-run",
                        "--env",
                        str(env_path),
                        "--repo-root",
                        str(root / "repo"),
                    ]
                )

            output = stdout.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Logbook backup", output)
            self.assertIn("execute=no", output)
            self.assertIn("ledger_job_count=1", output)
            self.assertIn("delete_audio=no", output)

            backup = run_backup(
                config=app_config,
                repo_root=root / "repo",
                env_path=env_path,
                backup_root=root / "backups",
                execute=True,
            )
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "backup-restore-drill",
                        "--env",
                        str(env_path),
                        "--backup",
                        str(backup.backup_dir),
                    ]
                )

            output = stdout.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Logbook restore drill", output)
            self.assertIn("status=ok", output)
            self.assertIn("job_count=1", output)

    def test_backup_dry_run_plans_restorable_non_audio_artifact_set(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            _seed_state(app_config, root)

            result = run_backup(
                config=app_config,
                repo_root=root / "repo",
                env_path=root / "repo" / ".env",
                backup_root=root / "backups",
                execute=False,
            )

            self.assertFalse(result.executed)
            self.assertEqual(result.audio_policy, "exclude_raw_and_quarantined_audio")
            self.assertEqual(result.ledger_job_count, 1)
            self.assertIn("config/.env.example", result.planned_relative_paths)
            self.assertIn("launchd/local.logbook.api.plist", result.planned_relative_paths)
            self.assertIn("transcripts/job-1.json", result.planned_relative_paths)
            self.assertIn("ledger/voice_ingest.sqlite", result.planned_relative_paths)
            self.assertNotIn("config/.env", result.planned_relative_paths)
            self.assertNotIn(".mp3", "\n".join(result.planned_relative_paths))
            self.assertFalse(result.backup_dir.exists())

    def test_backup_execute_and_restore_drill_validate_sqlite_copy_without_audio_or_secrets(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            _seed_state(app_config, root)

            result = run_backup(
                config=app_config,
                repo_root=root / "repo",
                env_path=root / "repo" / ".env",
                backup_root=root / "backups",
                execute=True,
            )

            self.assertTrue(result.executed)
            self.assertTrue(result.manifest_path.exists())
            self.assertTrue((result.backup_dir / "ledger" / "voice_ingest.sqlite").exists())
            self.assertFalse((result.backup_dir / "config" / ".env").exists())
            self.assertEqual(list(result.backup_dir.rglob("*.mp3")), [])

            drill = run_restore_drill(result.backup_dir)

            self.assertEqual(drill.status, "ok")
            self.assertEqual(drill.integrity_check, "ok")
            self.assertEqual(drill.job_count, 1)
            self.assertEqual(drill.expected_job_count, 1)
            self.assertEqual(drill.schema_version, 1)


def _seed_state(config: AppConfig, root: Path) -> None:
    repo_root = root / "repo"
    repo_root.mkdir(exist_ok=True)
    env_path = repo_root / ".env"
    token_lines = "LOGBOOK_ACTION_TOKEN=secret\nLOGBOOK_READ_TOKEN=secret\n"
    if env_path.exists():
        env_path.write_text(env_path.read_text(encoding="utf-8") + token_lines, encoding="utf-8")
    else:
        env_path.write_text(token_lines, encoding="utf-8")
    (repo_root / ".env.example").write_text("LOGBOOK_ACTION_TOKEN=\n", encoding="utf-8")
    launchd_dir = config.processing_root / "launchd"
    launchd_dir.mkdir(parents=True)
    (launchd_dir / "local.logbook.api.plist").write_text("<plist />\n", encoding="utf-8")
    transcript_dir = config.processing_root / "transcripts"
    transcript_dir.mkdir(parents=True)
    (transcript_dir / "job-1.json").write_text('{"text":"hello"}\n', encoding="utf-8")
    trash_audio = config.processing_root / "trash" / "local-audio"
    trash_audio.mkdir(parents=True)
    (trash_audio / "260429_0821.mp3").write_bytes(b"quarantined audio")

    recording_path = config.recorder.recordings_dir / "260429_0821.mp3"
    recording_path.parent.mkdir(parents=True)
    recording_path.write_bytes(b"source audio")
    timestamp = datetime(2026, 4, 29, 8, 21, 54).timestamp()
    os.utime(recording_path, (timestamp, timestamp))
    candidate = discover_recordings(config.recorder.recordings_dir)[0]

    ledger = open_ledger(config.sqlite_path, initialize=True)
    try:
        ledger.record_discovery(candidate, "abc123", "IC RECORDER")
    finally:
        ledger.close()


def _write_env(root: Path) -> Path:
    repo_root = root / "repo"
    repo_root.mkdir(exist_ok=True)
    env_path = repo_root / ".env"
    env_path.write_text(
        "\n".join(
            [
                f"LOGBOOK_PROCESSING_ROOT={root / 'VoiceIngest'}",
                f"LOGBOOK_SQLITE_PATH={root / 'VoiceIngest' / 'voice_ingest.sqlite'}",
                f"LOGBOOK_BACKUP_ROOT={root / 'backups'}",
                "SONY_RECORDER_VOLUME_NAME=IC RECORDER",
                f"SONY_RECORDER_MOUNT_PATH={root / 'IC RECORDER'}",
                "SONY_RECORDER_RECORDINGS_PATH=/REC_FILE/FOLDER01",
                "ODIN_API_BASE_URL=http://odin.test",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return env_path


def _app_config(root: Path) -> AppConfig:
    mount = root / "IC RECORDER"
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
