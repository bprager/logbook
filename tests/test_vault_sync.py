from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from logbook.cli import _mark_vault_synced_and_sync_memory
from logbook.config import AppConfig, ObsidianConfig, OdinConfig, RecorderConfig, RetentionConfig
from logbook.ledger import open_ledger
from logbook.recorder import RecordingCandidate
from logbook.vault_sync import mark_vault_synced_jobs


class VaultSyncMarkTests(TestCase):
    def test_dry_run_marks_pushed_final_jobs_as_markable_without_mutating_ledger(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _app_config(root)
            _init_pushed_vault(
                config.obsidian.vault_local_path,
                [
                    "10 - Logs/00 - Inbox/2026/04-April/entry.md",
                    "06 - Timestamps/2026/04-April/2026-04-29-Wednesday-Log.md",
                ],
            )
            job = _consolidated_job(config)

            result = mark_vault_synced_jobs(config, dry_run=True)

            self.assertEqual(result.markable_count, 1)
            self.assertEqual(result.marked_count, 0)
            self.assertEqual(result.blocked_count, 0)
            ledger = open_ledger(config.sqlite_path)
            try:
                updated = ledger.get_by_checksum(job.checksum_sha256)
            finally:
                ledger.close()
            self.assertIsNotNone(updated)
            self.assertIsNone(updated.vault_synced_at)

    def test_execute_marks_pushed_final_jobs_as_vault_synced(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _app_config(root)
            _init_pushed_vault(
                config.obsidian.vault_local_path,
                [
                    "20 - Notes/00 - Inbox/task/2026-04-29T08-21-00-job-000001-task.md",
                ],
            )
            job = _category_job(config)

            result = mark_vault_synced_jobs(
                config,
                dry_run=False,
                synced_at="2026-04-29T20:00:00+00:00",
            )

            self.assertEqual(result.marked_count, 1)
            self.assertEqual(result.blocked_count, 0)
            ledger = open_ledger(config.sqlite_path)
            try:
                updated = ledger.get_by_checksum(job.checksum_sha256)
            finally:
                ledger.close()
            self.assertIsNotNone(updated)
            self.assertEqual(updated.vault_synced_at, "2026-04-29T20:00:00+00:00")

    def test_mount_processing_recovers_clean_pushed_job_before_vault_sync_mark(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _app_config(root)
            _init_pushed_vault(
                config.obsidian.vault_local_path,
                [
                    "20 - Notes/00 - Inbox/task/2026-04-29T08-21-00-job-000001-task.md",
                ],
            )
            job = _category_job(config)

            recovered = _mark_vault_synced_and_sync_memory(config, Path(".env"))

            self.assertTrue(recovered)
            ledger = open_ledger(config.sqlite_path)
            try:
                updated = ledger.get_by_checksum(job.checksum_sha256)
            finally:
                ledger.close()
            self.assertIsNotNone(updated)
            self.assertIsNotNone(updated.vault_synced_at)

    def test_mount_processing_publishes_local_generated_note_before_vault_sync_mark(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _app_config(root)
            _init_pushed_vault(config.obsidian.vault_local_path, [])
            job = _meeting_job(config)
            note_path = config.obsidian.vault_local_path / job.obsidian_path
            note_path.parent.mkdir(parents=True, exist_ok=True)
            note_path.write_text("generated meeting\n", encoding="utf-8")

            recovered = _mark_vault_synced_and_sync_memory(config, Path(".env"))

            self.assertTrue(recovered)
            _git(config.obsidian.vault_local_path, "cat-file", "-e", f"HEAD:{job.obsidian_path}")
            self.assertEqual(_git_output(config.obsidian.vault_local_path, "status", "--short"), "")
            ledger = open_ledger(config.sqlite_path)
            try:
                updated = ledger.get_by_checksum(job.checksum_sha256)
            finally:
                ledger.close()
            self.assertIsNotNone(updated)
            self.assertIsNotNone(updated.vault_synced_at)

    def test_blocks_when_vault_head_is_not_pushed_to_origin(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _app_config(root)
            _init_pushed_vault(
                config.obsidian.vault_local_path,
                [
                    "20 - Notes/00 - Inbox/task/2026-04-29T08-21-00-job-000001-task.md",
                ],
            )
            _git(config.obsidian.vault_local_path, "commit", "--allow-empty", "-m", "unpushed")
            _category_job(config)

            result = mark_vault_synced_jobs(config, dry_run=True)

            self.assertEqual(result.markable_count, 0)
            self.assertEqual(result.blocked_count, 1)
            self.assertIn("vault_head_not_pushed", result.items[0].blockers)

    def test_blocks_when_required_vault_path_is_missing_from_head(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _app_config(root)
            _init_pushed_vault(config.obsidian.vault_local_path, [])
            _category_job(config)

            result = mark_vault_synced_jobs(config, dry_run=True)

            self.assertEqual(result.markable_count, 0)
            self.assertEqual(result.blocked_count, 1)
            self.assertTrue(
                any(blocker.startswith("missing_in_vault_head:") for blocker in result.items[0].blockers)
            )

    def test_blocks_previously_synced_job_when_required_path_is_missing_from_head(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _app_config(root)
            _init_pushed_vault(config.obsidian.vault_local_path, [])
            job = _category_job(config)
            ledger = open_ledger(config.sqlite_path)
            try:
                ledger.mark_vault_synced(job.checksum_sha256, "2026-04-29T20:00:00+00:00")
            finally:
                ledger.close()

            result = mark_vault_synced_jobs(config, dry_run=True)

            self.assertEqual(result.already_synced_count, 0)
            self.assertEqual(result.blocked_count, 1)
            self.assertEqual(result.items[0].status, "blocked")
            self.assertTrue(
                any(blocker.startswith("missing_in_vault_head:") for blocker in result.items[0].blockers)
            )


def _consolidated_job(config: AppConfig):
    ledger = open_ledger(config.sqlite_path, initialize=True)
    try:
        job = ledger.record_discovery(
            _candidate(config.recorder.recordings_dir / "260429_0821.mp3"),
            checksum_sha256="a" * 64,
            source_device="IC RECORDER",
        )
        ledger.mark_routed(
            job.checksum_sha256,
            classification="log",
            obsidian_path=Path("10 - Logs/00 - Inbox/2026/04-April/entry.md"),
            status="inbox_written",
        )
        return ledger.mark_consolidated(
            job.checksum_sha256,
            daily_log_path=Path("06 - Timestamps/2026/04-April/2026-04-29-Wednesday-Log.md"),
        )
    finally:
        ledger.close()


def _category_job(config: AppConfig):
    ledger = open_ledger(config.sqlite_path, initialize=True)
    try:
        job = ledger.record_discovery(
            _candidate(config.recorder.recordings_dir / "260429_0821.mp3"),
            checksum_sha256="b" * 64,
            source_device="IC RECORDER",
        )
        return ledger.mark_routed(
            job.checksum_sha256,
            classification="category:task",
            obsidian_path=Path("20 - Notes/00 - Inbox/task/2026-04-29T08-21-00-job-000001-task.md"),
            status="category_written",
        )
    finally:
        ledger.close()


def _meeting_job(config: AppConfig):
    ledger = open_ledger(config.sqlite_path, initialize=True)
    try:
        job = ledger.record_discovery(
            _candidate(config.recorder.recordings_dir / "260429_0821.mp3"),
            checksum_sha256="c" * 64,
            source_device="IC RECORDER",
        )
        return ledger.mark_routed(
            job.checksum_sha256,
            classification="meeting",
            obsidian_path=Path("30 - Meetings/2026/04-April/2026-04-29T08-21-00-job-000001-meeting.md"),
            status="meeting_written",
        )
    finally:
        ledger.close()


def _candidate(path: Path) -> RecordingCandidate:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake mp3")
    timestamp = datetime(2026, 4, 29, 8, 21, 0).timestamp()
    os.utime(path, (timestamp, timestamp))
    stat = path.stat()
    return RecordingCandidate(
        path=path,
        filename=path.name,
        size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime).astimezone(),
        parsed_recorded_at=datetime(2026, 4, 29, 8, 21),
        timestamp_matches_mtime=True,
        sequence=None,
    )


def _app_config(root: Path) -> AppConfig:
    vault_root = root / "vault"
    processing_root = root / "VoiceIngest"
    recorder_root = root / "IC RECORDER"
    recordings_dir = recorder_root / "REC_FILE" / "FOLDER01"
    recordings_dir.mkdir(parents=True)
    return AppConfig(
        processing_root=processing_root,
        sqlite_path=processing_root / "voice_ingest.sqlite",
        recorder=RecorderConfig(
            volume_name="IC RECORDER",
            mount_path=recorder_root,
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
        retention=RetentionConfig(hours=24, cleanup_mode="trash_then_delete"),
        obsidian=ObsidianConfig(
            cli_bin=str(_fake_cli(root)),
            vault_repo_url="https://github.com/bprager/obs-vault.git",
            vault_local_path=vault_root,
            sync_command="git -C {vault_path} pull --ff-only",
            stage_command='git -C {vault_path} add -- "06 - Timestamps" "10 - Logs" "20 - Notes" "30 - Meetings" "40 - Reviews" "99 - Dead Letters"',
            status_command="git -C {vault_path} status --short",
            commit_command='git -C {vault_path} commit -m "{message}"',
            push_command="git -C {vault_path} push origin main",
        ),
    )


def _init_pushed_vault(vault_root: Path, relative_paths: list[str]) -> None:
    remote = vault_root.parent / "origin.git"
    _git(vault_root.parent, "init", "--bare", str(remote))
    _git(vault_root.parent, "init", "-b", "main", str(vault_root))
    _git(vault_root, "config", "user.email", "logbook@example.test")
    _git(vault_root, "config", "user.name", "Logbook Test")
    for relative_path in relative_paths:
        path = vault_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("generated\n", encoding="utf-8")
    (vault_root / ".keep").write_text("keep\n", encoding="utf-8")
    _git(vault_root, "add", ".")
    _git(vault_root, "commit", "-m", "generated notes")
    _git(vault_root, "remote", "add", "origin", str(remote))
    _git(vault_root, "push", "-u", "origin", "main")


def _fake_cli(root: Path) -> Path:
    cli = root / "fake-obsidian"
    cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(cli, 0o755)
    return cli


def _git(cwd: Path, *args: str) -> None:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed: {completed.stdout}\n{completed.stderr}"
        )


def _git_output(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed: {completed.stdout}\n{completed.stderr}"
        )
    return completed.stdout.strip()
