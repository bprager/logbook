from __future__ import annotations

import plistlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from logbook.config import AppConfig


DEFAULT_API_LABEL = "local.logbook.api"
DEFAULT_MOUNT_PROBE_LABEL = "local.logbook.recorder.mount-probe"
DEFAULT_RETENTION_AUDIT_LABEL = "local.logbook.retention-audit"
DEFAULT_ENTITY_LINKER_LABEL = "local.logbook.entity-linker"


@dataclass(frozen=True)
class LaunchdPlist:
    filename: str
    label: str
    content: str


@dataclass(frozen=True)
class LaunchdAppBundle:
    bundle_path: Path
    info_plist_content: str
    source_content: str

    @property
    def info_plist_path(self) -> Path:
        return self.bundle_path / "Contents" / "Info.plist"

    @property
    def source_path(self) -> Path:
        return self.bundle_path / "Contents" / "MacOS" / "LogbookMountRunner.c"

    @property
    def executable_path(self) -> Path:
        return self.bundle_path / "Contents" / "MacOS" / "LogbookMountRunner"


@dataclass(frozen=True)
class LaunchdPackage:
    output_dir: Path
    logs_dir: Path
    mount_runner: LaunchdAppBundle
    api_service: LaunchdPlist
    mount_probe: LaunchdPlist
    retention_audit: LaunchdPlist
    entity_linker: LaunchdPlist

    @property
    def plists(self) -> tuple[LaunchdPlist, ...]:
        return (
            self.api_service,
            self.mount_probe,
            self.retention_audit,
            self.entity_linker,
        )


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
    entity_linker_label: str = DEFAULT_ENTITY_LINKER_LABEL,
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
    mount_runner = _render_mount_runner_app(
        bundle_path=output_dir / "LogbookMountRunner.app",
        python_executable=python_executable,
        repo_root=repo_root,
        src_path=src_path,
        env_path=env_path,
        recorder_dir=config.recorder.recordings_dir,
    )
    mount_probe_plist = {
        "Label": mount_probe_label,
        "ProgramArguments": [
            "/usr/bin/open",
            "-W",
            "-n",
            str(mount_runner.bundle_path),
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
            "cleanup-audio",
            "--env",
            str(env_path),
            "--execute",
            "--include-recorder",
        ],
        "RunAtLoad": False,
        "StartCalendarInterval": [{"Minute": 17}],
        "StandardOutPath": str(logs_dir / "logbook-retention-audit.out.log"),
        "StandardErrorPath": str(logs_dir / "logbook-retention-audit.err.log"),
        **common,
    }
    entity_linker_plist = {
        "Label": entity_linker_label,
        "ProgramArguments": [
            python_executable,
            "-m",
            "logbook.cli",
            "link-daily-log-entities",
            "--env",
            str(env_path),
            "--months",
            "3",
            "--execute",
        ],
        "RunAtLoad": False,
        "StartCalendarInterval": [{"Hour": 3, "Minute": 37}],
        "StandardOutPath": str(logs_dir / "logbook-entity-linker.out.log"),
        "StandardErrorPath": str(logs_dir / "logbook-entity-linker.err.log"),
        **common,
    }

    return LaunchdPackage(
        output_dir=output_dir,
        logs_dir=logs_dir,
        mount_runner=mount_runner,
        api_service=_render_plist(api_label, api_plist),
        mount_probe=_render_plist(mount_probe_label, mount_probe_plist),
        retention_audit=_render_plist(retention_audit_label, retention_audit_plist),
        entity_linker=_render_plist(entity_linker_label, entity_linker_plist),
    )


def write_launchd_package(package: LaunchdPackage) -> tuple[Path, ...]:
    package.output_dir.mkdir(parents=True, exist_ok=True)
    package.logs_dir.mkdir(parents=True, exist_ok=True)
    _write_app_bundle(package.mount_runner)
    written_paths: list[Path] = []
    for plist in package.plists:
        path = package.output_dir / plist.filename
        path.write_text(plist.content, encoding="utf-8")
        written_paths.append(path)
    return tuple(written_paths)


def _render_plist(label: str, data: dict) -> LaunchdPlist:
    content = plistlib.dumps(data, sort_keys=False).decode("utf-8")
    return LaunchdPlist(filename=f"{label}.plist", label=label, content=content)


def _render_mount_runner_app(
    *,
    bundle_path: Path,
    python_executable: str,
    repo_root: Path,
    src_path: Path,
    env_path: Path,
    recorder_dir: Path,
) -> LaunchdAppBundle:
    info = {
        "CFBundleIdentifier": "ws.prager.logbook.mount-runner",
        "CFBundleName": "Logbook Mount Runner",
        "CFBundleDisplayName": "Logbook Mount Runner",
        "CFBundleExecutable": "LogbookMountRunner",
        "CFBundlePackageType": "APPL",
        "CFBundleVersion": "1.0",
        "CFBundleShortVersionString": "1.0",
        "NSRemovableVolumesUsageDescription": (
            "Logbook imports recordings from the Sony ICD-PX370 mounted volume."
        ),
    }
    info_content = plistlib.dumps(info, sort_keys=False).decode("utf-8")
    source_content = "\n".join(
        [
            "#include <dirent.h>",
            "#include <errno.h>",
            "#include <spawn.h>",
            "#include <stdio.h>",
            "#include <stdlib.h>",
            "#include <string.h>",
            "#include <sys/wait.h>",
            "#include <unistd.h>",
            "",
            "extern char **environ;",
            "",
            "int main(void) {",
            f"    if (chdir({_c_string(str(repo_root))}) != 0) {{",
            '        perror("chdir");',
            "        return 111;",
            "    }",
            f"    if (setenv(\"PYTHONPATH\", {_c_string(str(src_path))}, 1) != 0) {{",
            '        perror("setenv");',
            "        return 112;",
            "    }",
            f"    DIR *recorder_dir = opendir({_c_string(str(recorder_dir))});",
            "    if (recorder_dir != NULL) {",
            "        closedir(recorder_dir);",
            "    } else {",
            "        fprintf(stderr, \"recorder preflight failed: %s\\n\", strerror(errno));",
            "    }",
            "    char *const argv[] = {",
            f"        {_c_string(python_executable)},",
            '        "-m",',
            '        "logbook.cli",',
            '        "process-mounted-recorder",',
            '        "--env",',
            f"        {_c_string(str(env_path))},",
            "        NULL,",
            "    };",
            "    pid_t pid;",
            f"    int spawn_status = posix_spawn(&pid, {_c_string(python_executable)}, "
            "NULL, NULL, argv, environ);",
            "    if (spawn_status != 0) {",
            "        errno = spawn_status;",
            '        perror("posix_spawn");',
            "        return spawn_status;",
            "    }",
            "    int status = 0;",
            "    if (waitpid(pid, &status, 0) < 0) {",
            '        perror("waitpid");',
            "        return errno == 0 ? 127 : errno;",
            "    }",
            "    if (WIFEXITED(status)) {",
            "        return WEXITSTATUS(status);",
            "    }",
            "    if (WIFSIGNALED(status)) {",
            "        return 128 + WTERMSIG(status);",
            "    }",
            "    return 127;",
            "}",
            "",
        ]
    )
    return LaunchdAppBundle(
        bundle_path=bundle_path,
        info_plist_content=info_content,
        source_content=source_content,
    )


def _write_app_bundle(app: LaunchdAppBundle) -> None:
    app.executable_path.parent.mkdir(parents=True, exist_ok=True)
    app.info_plist_path.write_text(app.info_plist_content, encoding="utf-8")
    app.source_path.write_text(app.source_content, encoding="utf-8")
    completed = subprocess.run(
        ["cc", str(app.source_path), "-o", str(app.executable_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"failed to compile mount runner app: {detail}")
    app.executable_path.chmod(0o755)


def _c_string(value: str) -> str:
    return json.dumps(value)
