from __future__ import annotations

import plistlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory

from logbook.config import AppConfig
from logbook.markdown import atomic_write_text


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
        return self.bundle_path / "Contents" / "Resources" / "LogbookMountRunner.c"

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
        recorder_mount_path=config.recorder.mount_path,
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


def write_launchd_package(
    package: LaunchdPackage, *, signing_identity: str | None = None,
) -> tuple[Path, ...]:
    package.output_dir.mkdir(parents=True, exist_ok=True)
    package.logs_dir.mkdir(parents=True, exist_ok=True)
    identity_path = package.output_dir / "signing-identity"
    previous_identity = identity_path.read_text(encoding="utf-8") if identity_path.exists() else None
    if signing_identity is None and identity_path.exists():
        signing_identity = (previous_identity or "").strip()
    if signing_identity is not None:
        if not signing_identity.strip() or signing_identity.strip() == "-":
            raise ValueError("use a permanent signing certificate; temporary signing loses permissions")
        atomic_write_text(identity_path, signing_identity.strip() + "\n")
        try:
            _write_signed_app_bundle(package.mount_runner, signing_identity.strip())
        except Exception:
            if previous_identity is not None:
                atomic_write_text(identity_path, previous_identity)
            raise
    else:
        if sys.platform == "darwin" and package.mount_runner.bundle_path.exists():
            signature = subprocess.run(
                ["/usr/bin/codesign", "--display", "--verbose=2",
                 str(package.mount_runner.bundle_path)], capture_output=True, text=True, check=False,
            )
            if "Authority=" in signature.stderr:
                raise ValueError("existing helper is certificate-signed; specify its signing identity")
            if not (
                signature.returncode == 0 and "Signature=adhoc" in signature.stderr
                or "code object is not signed at all" in signature.stderr
            ):
                raise ValueError(f"cannot establish existing helper signing identity: {signature.stderr.strip()}")
        _write_app_bundle(package.mount_runner)
    written_paths: list[Path] = []
    for plist in package.plists:
        path = package.output_dir / plist.filename
        path.write_text(plist.content, encoding="utf-8")
        written_paths.append(path)
    return tuple(written_paths)


def _write_signed_app_bundle(app: LaunchdAppBundle, signing_identity: str) -> None:
    """Replace the installed helper only after its new signature is verified."""
    app.bundle_path.parent.mkdir(parents=True, exist_ok=True)
    identifier = plistlib.loads(app.info_plist_content.encode("utf-8"))["CFBundleIdentifier"]
    with TemporaryDirectory(
        prefix=".mount-build-", dir=app.bundle_path.parent, ignore_cleanup_errors=True,
    ) as tmp:
        staged = replace(app, bundle_path=Path(tmp) / app.bundle_path.name)
        _write_app_bundle(staged)
        subprocess.run(
            ["/usr/bin/codesign", "--force", "--sign", signing_identity,
             "--identifier", identifier, str(staged.bundle_path)], check=True,
        )
        subprocess.run(
            ["/usr/bin/codesign", "--verify", "--strict", str(staged.bundle_path)], check=True,
        )
        previous = app.bundle_path.with_name(app.bundle_path.name + ".previous")
        if previous.exists():
            raise FileExistsError(f"recover the previous helper before rebuilding: {previous}")
        if app.bundle_path.exists():
            app.bundle_path.rename(previous)
        try:
            staged.bundle_path.rename(app.bundle_path)
        except OSError:
            if previous.exists():
                previous.rename(app.bundle_path)
            raise
        if previous.exists():
            shutil.rmtree(previous, ignore_errors=True)


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
    recorder_mount_path: Path | None = None,
    module: str = "logbook.cli",
    command: str = "process-mounted-recorder",
    config_arg: str = "--env",
    bundle_identifier: str = "ws.prager.logbook.mount-runner",
    display_name: str = "Logbook Mount Runner",
) -> LaunchdAppBundle:
    info = {
        "CFBundleIdentifier": bundle_identifier,
        "CFBundleName": display_name,
        "CFBundleDisplayName": display_name,
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
            *(
                [
                    f"    if (access({_c_string(str(recorder_mount_path))}, F_OK) != 0 && errno == ENOENT) {{",
                    '        printf("recorder absent; no import requested\\n");',
                    "        return 0;",
                    "    }",
                ]
                if recorder_mount_path is not None
                else []
            ),
            f"    DIR *recorder_dir = opendir({_c_string(str(recorder_dir))});",
            "    if (recorder_dir != NULL) {",
            "        closedir(recorder_dir);",
            "    } else {",
            "        fprintf(stderr, \"recorder preflight failed: %s\\n\", strerror(errno));",
            "    }",
            "    char *const argv[] = {",
            f"        {_c_string(python_executable)},",
            '        "-m",',
            f"        {_c_string(module)},",
            f"        {_c_string(command)},",
            f"        {_c_string(config_arg)},",
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


def _write_app_bundle(app: LaunchdAppBundle, *, compiler: tuple[str, ...] = ("cc",)) -> None:
    app.executable_path.parent.mkdir(parents=True, exist_ok=True)
    app.source_path.parent.mkdir(parents=True, exist_ok=True)
    app.info_plist_path.write_text(app.info_plist_content, encoding="utf-8")
    app.source_path.write_text(app.source_content, encoding="utf-8")
    completed = subprocess.run(
        [*compiler, str(app.source_path), "-o", str(app.executable_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"failed to compile mount runner app: {detail}")
    app.executable_path.chmod(0o755)
    # Repair packages generated before source files were moved out of MacOS.
    # codesign treats files there as nested executables, not data resources.
    (app.executable_path.parent / "LogbookMountRunner.c").unlink(missing_ok=True)


def _c_string(value: str) -> str:
    return json.dumps(value)
