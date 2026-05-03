from __future__ import annotations

import json
import shlex
import shutil
import socket
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from logbook.config import AppConfig
from logbook.ledger import SCHEMA_VERSION, open_ledger


AUDIO_POLICY = "exclude_raw_and_quarantined_audio"
SECRET_POLICY = "exclude_live_env_and_tokens"
MANIFEST_NAME = "manifest.json"
BACKUP_PREFIX = "logbook-backup-"
NON_AUDIO_STATE_DIRS = ("transcripts", "diarization", "insights")
AUDIO_SUFFIXES = {".aac", ".aiff", ".flac", ".m4a", ".mp3", ".ogg", ".wav", ".wma"}


@dataclass(frozen=True)
class BackupResult:
    backup_id: str
    backup_root: str
    backup_dir: Path
    manifest_path: Path
    executed: bool
    remote_target: str | None
    ledger_job_count: int
    action_audit_count: int
    planned_relative_paths: list[str]
    copied_relative_paths: list[str]
    audio_policy: str = AUDIO_POLICY
    secret_policy: str = SECRET_POLICY


@dataclass(frozen=True)
class RestoreDrillResult:
    backup_dir: Path
    manifest_path: Path
    ledger_path: Path
    status: str
    integrity_check: str
    schema_version: int | None
    expected_job_count: int | None
    job_count: int
    action_audit_count: int


def run_backup(
    *,
    config: AppConfig,
    repo_root: Path,
    env_path: Path,
    backup_root: Path | str,
    execute: bool = False,
    ssh_identity_file: str | None = None,
    now: datetime | None = None,
) -> BackupResult:
    timestamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    backup_id = f"{BACKUP_PREFIX}{timestamp}"
    backup_root_text = str(backup_root)
    remote_root = backup_root_text if _is_remote_location(backup_root_text) else None
    local_root = (
        config.processing_root / "backups" / "staging"
        if remote_root is not None
        else Path(backup_root)
    )
    backup_dir = local_root / backup_id
    manifest_path = backup_dir / MANIFEST_NAME

    planned_paths = _planned_relative_paths(config=config, repo_root=repo_root)
    ledger_job_count, action_audit_count = _ledger_counts(config.sqlite_path)

    copied_paths: list[str] = []
    remote_target = f"{remote_root.rstrip('/')}/{backup_id}" if remote_root else None
    if execute:
        if backup_dir.exists():
            raise ValueError(f"backup directory already exists: {backup_dir}")
        backup_dir.mkdir(parents=True)
        copied_paths = _write_backup_artifacts(
            config=config,
            repo_root=repo_root,
            env_path=env_path,
            backup_dir=backup_dir,
        )
        manifest = _manifest(
            backup_id=backup_id,
            backup_root=backup_root_text,
            remote_target=remote_target,
            ledger_name=config.sqlite_path.name,
            ledger_job_count=ledger_job_count,
            action_audit_count=action_audit_count,
            relative_paths=copied_paths,
        )
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        copied_paths = sorted(copied_paths + [MANIFEST_NAME])
        if remote_target is not None:
            _copy_directory_to_remote(
                backup_dir,
                remote_target,
                ssh_identity_file=ssh_identity_file,
            )

    return BackupResult(
        backup_id=backup_id,
        backup_root=backup_root_text,
        backup_dir=backup_dir,
        manifest_path=manifest_path,
        executed=execute,
        remote_target=remote_target,
        ledger_job_count=ledger_job_count,
        action_audit_count=action_audit_count,
        planned_relative_paths=planned_paths,
        copied_relative_paths=copied_paths,
    )


def run_restore_drill(
    backup_location: Path | str,
    ssh_identity_file: str | None = None,
) -> RestoreDrillResult:
    location = str(backup_location)
    if _is_remote_location(location):
        with TemporaryDirectory() as tmp:
            local_dir = Path(tmp) / "backup"
            _copy_directory_from_remote(
                location,
                local_dir,
                ssh_identity_file=ssh_identity_file,
            )
            return _run_local_restore_drill(local_dir)
    return _run_local_restore_drill(Path(backup_location))


def _write_backup_artifacts(
    *,
    config: AppConfig,
    repo_root: Path,
    env_path: Path,
    backup_dir: Path,
) -> list[str]:
    copied: list[str] = []
    ledger_target = backup_dir / "ledger" / config.sqlite_path.name
    ledger_target.parent.mkdir(parents=True)
    _sqlite_backup(config.sqlite_path, ledger_target)
    copied.append(_relative(ledger_target, backup_dir))

    env_example = repo_root / ".env.example"
    if env_example.exists():
        _copy_file(env_example, backup_dir / "config" / ".env.example")
        copied.append("config/.env.example")

    # Intentionally prove the live env is excluded even when it is passed to the CLI.
    if env_path.name == ".env" and env_path.exists():
        pass

    launchd_root = config.processing_root / "launchd"
    if launchd_root.exists():
        for source in sorted(launchd_root.glob("*.plist")):
            target = backup_dir / "launchd" / source.name
            _copy_file(source, target)
            copied.append(_relative(target, backup_dir))

    for dirname in NON_AUDIO_STATE_DIRS:
        state_root = config.processing_root / dirname
        if state_root.exists():
            for source in sorted(path for path in state_root.rglob("*") if path.is_file()):
                if _is_audio_path(source):
                    continue
                target = backup_dir / dirname / source.relative_to(state_root)
                _copy_file(source, target)
                copied.append(_relative(target, backup_dir))

    return sorted(copied)


def _planned_relative_paths(*, config: AppConfig, repo_root: Path) -> list[str]:
    paths = ["ledger/" + config.sqlite_path.name]
    if (repo_root / ".env.example").exists():
        paths.append("config/.env.example")
    launchd_root = config.processing_root / "launchd"
    if launchd_root.exists():
        paths.extend(f"launchd/{path.name}" for path in sorted(launchd_root.glob("*.plist")))
    for dirname in NON_AUDIO_STATE_DIRS:
        state_root = config.processing_root / dirname
        if state_root.exists():
            for path in sorted(candidate for candidate in state_root.rglob("*") if candidate.is_file()):
                if not _is_audio_path(path):
                    paths.append(str(Path(dirname) / path.relative_to(state_root)))
    return sorted(paths)


def _sqlite_backup(source: Path, target: Path) -> None:
    if not source.exists():
        raise ValueError(f"sqlite ledger not found: {source}")
    source_conn = sqlite3.connect(source)
    try:
        target_conn = sqlite3.connect(target)
        try:
            source_conn.backup(target_conn)
            target_conn.execute("PRAGMA optimize")
        finally:
            target_conn.close()
    finally:
        source_conn.close()


def _ledger_counts(sqlite_path: Path) -> tuple[int, int]:
    if not sqlite_path.exists():
        raise ValueError(f"sqlite ledger not found: {sqlite_path}")
    ledger = open_ledger(sqlite_path)
    try:
        job_count = len(ledger.all_jobs())
        row = ledger.connection.execute("SELECT COUNT(*) AS count FROM action_audit").fetchone()
        action_count = int(row["count"]) if row is not None else 0
    finally:
        ledger.close()
    return job_count, action_count


def _run_local_restore_drill(backup_dir: Path) -> RestoreDrillResult:
    manifest_path = backup_dir / MANIFEST_NAME
    if not manifest_path.exists():
        raise ValueError(f"manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ledger_rel = manifest.get("ledger", {}).get("relative_path", "ledger/voice_ingest.sqlite")
    ledger_path = backup_dir / ledger_rel
    if not ledger_path.exists():
        raise ValueError(f"backup ledger not found: {ledger_path}")

    conn = sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True)
    try:
        integrity_row = conn.execute("PRAGMA integrity_check").fetchone()
        integrity = str(integrity_row[0]) if integrity_row else "missing"
        schema_row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        schema_version = int(schema_row[0]) if schema_row and schema_row[0] is not None else None
        job_count = int(conn.execute("SELECT COUNT(*) FROM recording_jobs").fetchone()[0])
        action_count = int(conn.execute("SELECT COUNT(*) FROM action_audit").fetchone()[0])
    finally:
        conn.close()

    expected_job_count = manifest.get("ledger", {}).get("job_count")
    status = (
        "ok"
        if integrity == "ok"
        and schema_version == SCHEMA_VERSION
        and (expected_job_count is None or job_count == int(expected_job_count))
        else "failed"
    )
    return RestoreDrillResult(
        backup_dir=backup_dir,
        manifest_path=manifest_path,
        ledger_path=ledger_path,
        status=status,
        integrity_check=integrity,
        schema_version=schema_version,
        expected_job_count=int(expected_job_count) if expected_job_count is not None else None,
        job_count=job_count,
        action_audit_count=action_count,
    )


def _manifest(
    *,
    backup_id: str,
    backup_root: str,
    remote_target: str | None,
    ledger_name: str,
    ledger_job_count: int,
    action_audit_count: int,
    relative_paths: list[str],
) -> dict:
    return {
        "schema_version": 1,
        "backup_id": backup_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": socket.gethostname(),
        "backup_root": backup_root,
        "remote_target": remote_target,
        "audio_policy": AUDIO_POLICY,
        "secret_policy": SECRET_POLICY,
        "ledger": {
            "relative_path": f"ledger/{ledger_name}",
            "backup_method": "sqlite3.Connection.backup",
            "job_count": ledger_job_count,
            "action_audit_count": action_audit_count,
        },
        "artifacts": [{"path": path} for path in sorted(relative_paths)],
        "excluded": [
            ".env",
            "inbox/*.mp3",
            "trash/local-audio/*.mp3",
            "recorder source audio",
        ],
    }


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _is_audio_path(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_SUFFIXES


def _relative(path: Path, root: Path) -> str:
    return str(path.relative_to(root))


def _is_remote_location(location: str) -> bool:
    prefix = location.split(":", 1)[0]
    return ":" in location and bool(prefix) and "/" not in prefix


def backup_ssh_command(ssh_identity_file: str | None) -> list[str]:
    command = ["ssh"]
    if ssh_identity_file:
        command.extend(["-i", ssh_identity_file, "-o", "IdentitiesOnly=yes"])
    return command


def _copy_directory_to_remote(
    source_dir: Path,
    remote_target: str,
    *,
    ssh_identity_file: str | None,
) -> None:
    host, remote_path = remote_target.split(":", 1)
    subprocess.run(
        backup_ssh_command(ssh_identity_file)
        + [host, f"mkdir -p -- {shlex.quote(remote_path)}"],
        check=True,
    )
    rsync_command = ["rsync", "-a", "--delete"]
    if ssh_identity_file:
        rsync_command.extend(["-e", " ".join(shlex.quote(part) for part in backup_ssh_command(ssh_identity_file))])
    subprocess.run(
        rsync_command + [f"{source_dir}/", remote_target + "/"],
        check=True,
    )


def _copy_directory_from_remote(
    remote_source: str,
    local_target: Path,
    *,
    ssh_identity_file: str | None,
) -> None:
    local_target.mkdir(parents=True)
    rsync_command = ["rsync", "-a"]
    if ssh_identity_file:
        rsync_command.extend(["-e", " ".join(shlex.quote(part) for part in backup_ssh_command(ssh_identity_file))])
    subprocess.run(
        rsync_command + [remote_source.rstrip("/") + "/", f"{local_target}/"],
        check=True,
    )
