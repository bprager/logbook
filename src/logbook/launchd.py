from __future__ import annotations

import plistlib
import sys
from dataclasses import dataclass
from pathlib import Path

from logbook.config import AppConfig


DEFAULT_API_LABEL = "local.logbook.api"
DEFAULT_MOUNT_PROBE_LABEL = "local.logbook.recorder.mount-probe"
DEFAULT_RETENTION_AUDIT_LABEL = "local.logbook.retention-audit"


@dataclass(frozen=True)
class LaunchdPlist:
    filename: str
    label: str
    content: str


@dataclass(frozen=True)
class LaunchdPackage:
    output_dir: Path
    logs_dir: Path
    api_service: LaunchdPlist
    mount_probe: LaunchdPlist
    retention_audit: LaunchdPlist

    @property
    def plists(self) -> tuple[LaunchdPlist, LaunchdPlist, LaunchdPlist]:
        return (self.api_service, self.mount_probe, self.retention_audit)


def render_launchd_package(
    *,
    config: AppConfig,
    env_path: Path,
    output_dir: Path,
    repo_root: Path,
    python_bin: str | None = None,
    api_label: str = DEFAULT_API_LABEL,
    mount_probe_label: str = DEFAULT_MOUNT_PROBE_LABEL,
    retention_audit_label: str = DEFAULT_RETENTION_AUDIT_LABEL,
) -> LaunchdPackage:
    python_executable = python_bin or sys.executable
    src_path = repo_root / "src"
    logs_dir = config.processing_root / "logs"
    environment = {
        "PYTHONPATH": str(src_path),
    }
    common = {
        "WorkingDirectory": str(repo_root),
        "EnvironmentVariables": environment,
        "ProcessType": "Background",
    }

    api_plist = {
        "Label": api_label,
        "ProgramArguments": [
            python_executable,
            "-m",
            "logbook.cli",
            "serve-api",
            "--env",
            str(env_path),
        ],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 10,
        "ExitTimeOut": 15,
        "StandardOutPath": str(logs_dir / "logbook-api.out.log"),
        "StandardErrorPath": str(logs_dir / "logbook-api.err.log"),
        **common,
    }
    mount_probe_plist = {
        "Label": mount_probe_label,
        "ProgramArguments": [
            python_executable,
            "-m",
            "logbook.cli",
            "recorder-discover",
            "--env",
            str(env_path),
        ],
        "RunAtLoad": False,
        "StartOnMount": True,
        "StandardOutPath": str(logs_dir / "logbook-mount-probe.out.log"),
        "StandardErrorPath": str(logs_dir / "logbook-mount-probe.err.log"),
        **common,
    }
    retention_audit_plist = {
        "Label": retention_audit_label,
        "ProgramArguments": [
            python_executable,
            "-m",
            "logbook.cli",
            "retention-status",
            "--env",
            str(env_path),
        ],
        "RunAtLoad": False,
        "StartCalendarInterval": [{"Minute": 17}],
        "StandardOutPath": str(logs_dir / "logbook-retention-audit.out.log"),
        "StandardErrorPath": str(logs_dir / "logbook-retention-audit.err.log"),
        **common,
    }

    return LaunchdPackage(
        output_dir=output_dir,
        logs_dir=logs_dir,
        api_service=_render_plist(api_label, api_plist),
        mount_probe=_render_plist(mount_probe_label, mount_probe_plist),
        retention_audit=_render_plist(retention_audit_label, retention_audit_plist),
    )


def write_launchd_package(package: LaunchdPackage) -> tuple[Path, Path, Path]:
    package.output_dir.mkdir(parents=True, exist_ok=True)
    package.logs_dir.mkdir(parents=True, exist_ok=True)
    written_paths: list[Path] = []
    for plist in package.plists:
        path = package.output_dir / plist.filename
        path.write_text(plist.content, encoding="utf-8")
        written_paths.append(path)
    return (written_paths[0], written_paths[1], written_paths[2])


def _render_plist(label: str, data: dict) -> LaunchdPlist:
    content = plistlib.dumps(data, sort_keys=False).decode("utf-8")
    return LaunchdPlist(filename=f"{label}.plist", label=label, content=content)
