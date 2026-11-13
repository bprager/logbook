"""Signed source-runtime generations under the same standing delegation."""

from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import stat

from logbook import operator_bundle_bootstrap as bootstrap
from logbook.repair_release_delivery import fetch_candidate, REPOSITORY
from logbook.runtime_maintenance import atomic_file, controls, runtime_lock

BASE = Path("/Library/Application Support/LogbookPlanProducer")
GUARD = BASE / "maintenance"
SOURCE_HELPERS = {
    Path("/usr/local/libexec/logbook-lock-guardian"): "logbook-lock-guardian",
    Path("/usr/local/libexec/logbook-plan-producer"): "logbook-plan-producer",
    Path("/usr/local/libexec/logbook-maintenance-update"): "logbook-maintenance-update",
}


def source_runtime(base: Path = BASE) -> Path:
    pointer = base / "source.current"
    if not os.path.lexists(pointer):
        return base / "lib"
    info = pointer.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise ValueError("trusted source generation pointer required")
    digest = pointer.read_text().strip()
    import re

    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("invalid source generation")
    return base / "releases" / digest


def validate_source_generation(path: Path, digest: str) -> None:
    for current, directories, files in os.walk(path, followlinks=False):
        for item in (Path(current), *(Path(current) / name for name in directories + files)):
            info = item.lstat()
            if (
                stat.S_ISLNK(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o022
                or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))
            ):
                raise ValueError("untrusted source generation")
    if (path / ".bundle-ready").read_text().strip() != digest:
        raise ValueError("existing source generation mismatch")


def install_source_release(data: bytes, guard: Path = GUARD, base: Path = BASE) -> str:
    if bootstrap.signed_policy(guard)["schema"] != 4:
        raise ValueError("owner maintenance delegation required")
    with runtime_lock(guard), runtime_lock(guard, "release-update.lock"):
        release = bootstrap.approved_release(guard)
        if hashlib.sha256(data).hexdigest() != release["bundle_sha256"]:
            raise ValueError("maintenance package digest mismatch")
        before = controls(guard)
        current = source_runtime(base)
        if current.name == release["bundle_sha256"]:
            return "UNCHANGED"
        if current != base / "lib":
            installed = bootstrap.approved_release(guard, current)
            if release["sequence"] <= installed["sequence"]:
                raise ValueError("source maintenance release is stale or conflicts")
        releases = base / "releases"
        releases.mkdir(mode=0o755, exist_ok=True)
        candidate = guard / (".source-candidate-" + str(os.getpid()))
        destination = releases / release["bundle_sha256"]
        if not destination.exists():
            bootstrap.install_bundle(data, candidate, release["bundle_sha256"])
            selected = bootstrap.current_release_directory(guard)
            for name in ("release.json", "release.json.sig"):
                atomic_file(candidate / name, bootstrap._private_file(selected / name), 0o600)
            for path in candidate.rglob("*.py"):
                compile(path.read_bytes(), str(path), "exec")
            for path in candidate.rglob("*"):
                path.chmod(0o755 if path.is_dir() else 0o644)
            candidate.chmod(0o755)
            os.rename(candidate, destination)
            bootstrap.sync_directory(releases)
        validate_source_generation(destination, release["bundle_sha256"])
        if controls(guard) != before:
            raise ValueError("authority controls changed during maintenance")
        pointer = base / "source.current"
        prior_pointer = pointer.read_bytes() if pointer.exists() else None
        helper_before = {}
        for path in SOURCE_HELPERS:
            bootstrap.trusted_parent(path)
            info = path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o022
            ):
                raise ValueError("trusted fixed source helper required")
            helper_before[path] = (
                path.read_bytes(),
                stat.S_IMODE(info.st_mode),
                (info.st_uid, info.st_gid),
            )
        try:
            for path, name in SOURCE_HELPERS.items():
                content = (destination / "logbook/_entrypoints" / name).read_bytes()
                compile(content, str(path), "exec")
                atomic_file(path, content, helper_before[path][1], helper_before[path][2])
            atomic_file(pointer, (release["bundle_sha256"] + "\n").encode(), 0o644)
            atomic_file(
                guard / ("source-maintenance-" + str(release["sequence"]) + ".json"),
                json.dumps(
                    {
                        "release": release,
                        "authority_controls_preserved": True,
                        "previous_generation": str(current),
                        "owner_signature_requested": False,
                    }
                ).encode(),
                0o600,
            )
        except BaseException:
            for path, (content, mode, owner) in helper_before.items():
                atomic_file(path, content, mode, owner)
            if prior_pointer is None:
                if pointer.exists():
                    pointer.unlink()
                    bootstrap.sync_directory(base)
            else:
                atomic_file(pointer, prior_pointer, 0o644)
            raise
    return "UPDATED"


def main() -> None:  # pragma: no cover
    import sys

    if os.geteuid() != 0 or sys.argv[1:]:
        raise SystemExit("root and no arguments required")
    bootstrap.trusted_parent(GUARD / "check")
    if bootstrap.signed_policy(GUARD)["schema"] != 4:
        raise ValueError("owner maintenance delegation required")
    manifest, signature, data = fetch_candidate(REPOSITORY)
    # The source host has no protected graph executor; select the same ordered
    # release after validating its root-controlled policy and pinned signer.
    bootstrap.receive_release(GUARD, manifest, signature, _source=True)
    print(install_source_release(data))


if __name__ == "__main__":  # pragma: no cover
    main()
