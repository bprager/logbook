"""Routine signed runtime updates under explicit owner maintenance delegation.

The release publisher is trusted to supply privileged implementation code. This
is not isolation from malicious signed root code. Policy/trust changes still
require an owner signature; the installer preserves those control artifacts.
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time

from logbook import operator_bundle_bootstrap as bootstrap

HELPERS = {
    Path("/usr/local/libexec/logbook-repair-gateway"): "restricted_repair_gateway.py",
    Path("/usr/local/libexec/logbook-repair-dispatcher"): "named_repair_dispatcher.py",
    Path("/usr/local/libexec/logbook-repair-release-receiver"): "operator_bundle_bootstrap.py",
    Path("/usr/local/libexec/logbook-repair-bundle-installer"): "operator_bundle_bootstrap.py",
    Path("/usr/local/libexec/logbook-protected-executor-installer"): "operator_bundle_bootstrap.py",
}
CONTROL_NAMES = (
    "standing-policy.json",
    "standing-policy.json.sig",
    "approval.allowed_signers",
    "release.allowed_signers",
    "plan.allowed_signers",
    "executor-approval.json",
    "executor-approval.json.sig",
)


@contextmanager
def runtime_lock(guard: Path, name: str = "runtime-update.lock"):
    fd = os.open(guard / name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "r+b") as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("private runtime update lock required")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def assert_quiet() -> None:
    """Do not replace code under a live worker, recovery or future timer."""
    result = subprocess.run(
        [
            "/usr/bin/systemctl",
            "show",
            "--no-pager",
            "--property=Id,LoadState,ActiveState,SubState,MainPID,ControlPID,Result,Restart,NextElapseUSecMonotonic",
            "logbook-graph-maintenance-*",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    for block in result.stdout.strip().split("\n\n"):
        item = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if not item:
            continue
        if item.get("MainPID", "0") != "0" or item.get("ControlPID", "0") != "0":
            raise ValueError("maintenance worker or recovery is active")
        state = item.get("ActiveState")
        if state in {"inactive", "failed"}:
            continue
        if (
            state == "active"
            and item.get("SubState") == "exited"
            and item.get("Result") == "success"
            and item.get("Restart") in {"no", "on-failure"}
        ):
            continue
        if (
            item.get("Id", "").endswith(".timer")
            and state == "active"
            and item.get("NextElapseUSecMonotonic") == "infinity"
        ):
            continue
        raise ValueError("maintenance is not quiescent")


def controls(guard: Path) -> dict:
    result = {}
    for name in CONTROL_NAMES:
        path = guard / name
        if os.path.lexists(path):
            data = bootstrap._private_file(path)
            info = path.lstat()
            result[name] = (
                hashlib.sha256(data).hexdigest(),
                info.st_uid,
                info.st_gid,
                stat.S_IMODE(info.st_mode),
            )
    return result


def atomic_file(path: Path, data: bytes, mode: int, owner: tuple[int, int] | None = None) -> None:
    temporary = path.with_name(".maintenance-" + path.name + "-" + str(time.time_ns()))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as output:
            os.fchmod(output.fileno(), mode)
            if owner is not None:
                os.fchown(output.fileno(), *owner)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        bootstrap.sync_directory(path.parent)
    finally:
        if os.path.lexists(temporary):
            temporary.unlink()


def validate_executor(guard: Path) -> None:
    bootstrap.verify_protected_executor(guard)


def install_executor_release(data: bytes, guard: Path) -> str:
    """Install signed implementation without a per-version owner approval."""
    policy = bootstrap.signed_policy(guard)
    if policy["schema"] != 4:
        raise ValueError("owner maintenance delegation required")
    with runtime_lock(guard), runtime_lock(guard, "release-update.lock"):
        release = bootstrap.approved_release(guard)
        if hashlib.sha256(data).hexdigest() != release["bundle_sha256"]:
            raise ValueError("maintenance package digest mismatch")
        assert_quiet()
        before = controls(guard)
        current = guard / "executor"
        if os.path.lexists(current):
            installed = bootstrap.approved_release(guard, current)
            bootstrap.verify_protected_executor(guard)
            if installed == release:
                return "UNCHANGED"
            if release["sequence"] <= installed["sequence"]:
                raise ValueError("maintenance release is stale or conflicts")
        token = str(time.time_ns())
        candidate = guard / (".executor-candidate-" + token)
        backup = guard / ("executor.before-maintenance-" + token)
        bootstrap.install_bundle(data, candidate, release["bundle_sha256"])
        selected = bootstrap.current_release_directory(guard)
        for name in ("release.json", "release.json.sig"):
            atomic_file(candidate / name, bootstrap._private_file(selected / name), 0o600)
        if bootstrap.approved_release(guard, candidate) != release:
            raise ValueError("maintenance release changed during staging")
        for path in candidate.rglob("*.py"):
            compile(path.read_bytes(), str(path), "exec")
        helper_before = {}
        for path in HELPERS:
            bootstrap.trusted_parent(path)
            info = path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o022
            ):
                raise ValueError("trusted fixed maintenance helper required")
            helper_before[path] = (
                path.read_bytes(),
                stat.S_IMODE(info.st_mode),
                (info.st_uid, info.st_gid),
            )
        moved = switched = False
        try:
            if current.exists():
                os.rename(current, backup)
                moved = True
            os.rename(candidate, current)
            switched = True
            for path, name in HELPERS.items():
                atomic_file(
                    path,
                    (current / "logbook" / name).read_bytes(),
                    helper_before[path][1],
                    helper_before[path][2],
                )
            validate_executor(guard)
            if controls(guard) != before:
                raise ValueError("authority controls changed during maintenance")
        except BaseException:
            for path, (content, mode, owner) in helper_before.items():
                atomic_file(path, content, mode, owner)
            if switched:
                os.rename(current, guard / ("executor.failed-maintenance-" + token))
            if moved:
                os.rename(backup, current)
            bootstrap.sync_directory(guard)
            raise
        bootstrap.sync_directory(guard)
        atomic_file(
            guard / ("maintenance-receipt-" + token + ".json"),
            json.dumps(
                {
                    "release": release,
                    "authority_controls_preserved": True,
                    "previous_executor": str(backup) if moved else None,
                    "owner_signature_requested": False,
                }
            ).encode(),
            0o600,
        )
    return "UPDATED"
