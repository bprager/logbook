#!/usr/bin/python3
"""Standard-library-only installer for a private, validated maintenance bundle."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path, PurePosixPath

PROTECTED_DISPATCHER = Path('/usr/local/libexec/logbook-repair-dispatcher')


def trusted_parent(destination: Path) -> None:
    if not destination.is_absolute() or '..' in destination.parts:
        raise ValueError('trusted absolute path required')
    for current in reversed((destination.parent, *destination.parent.parents)):
        info = os.lstat(current)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError('trusted directory ancestry required')


def sync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def install_bundle(data: bytes, destination: Path, digest: str) -> None:
    if len(data) > 30000000 or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('bundle digest or size invalid')
    trusted_parent(destination)
    parent = destination.parent.lstat()
    if not destination.is_absolute() or not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
        raise ValueError('private bundle parent required')
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as archive:
        members = archive.getmembers()
        if sum(member.size for member in members) > 60000000:
            raise ValueError('bundle too large')
        for member in members:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or '..' in path.parts or not path.parts
                    or path.parts[0] not in {'logbook', 'neo4j', 'pytz'}
                    or not (member.isfile() or member.isdir())):
                raise ValueError('unsafe bundle path or type')
        destination.mkdir(mode=0o700)
        try:
            for member in members:
                path = destination.joinpath(*PurePosixPath(member.name).parts)
                if member.isdir():
                    path.mkdir(mode=0o700, parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    source = archive.extractfile(member)
                    if source is None:
                        raise ValueError('missing bundle contents')
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, 'wb') as output:
                        shutil.copyfileobj(source, output)
                        output.flush()
                        os.fsync(output.fileno())
            for current, _, _ in os.walk(destination, topdown=False):
                sync_directory(Path(current))
            fd = os.open(destination/'.bundle-ready', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as marker:
                marker.write(digest+'\n')
                marker.flush()
                os.fsync(marker.fileno())
            sync_directory(destination)
            sync_directory(destination.parent)
        except Exception:
            shutil.rmtree(destination)
            raise


def _private_file(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_size > 100000:
            raise ValueError('private policy file required')
        return source.read()


def approved_release(guard: Path, release_dir: Path | None = None) -> dict:
    """Read a separately signed release from the protected guard directory."""
    release_dir = guard if release_dir is None else release_dir
    info = release_dir.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('private release directory required')
    content = _private_file(release_dir/'release.json')
    allowed = guard/'release.allowed_signers'
    signature = release_dir/'release.json.sig'
    _private_file(allowed)
    _private_file(signature)
    result = subprocess.run(['/usr/bin/ssh-keygen', '-Y', 'verify', '-f', str(allowed),
                             '-I', 'release', '-n', 'logbook-repair-release', '-s', str(signature)],
                            input=content, capture_output=True, timeout=10)
    if result.returncode:
        raise ValueError('release signature invalid')
    try:
        release = json.loads(content)
        if (not isinstance(release, dict) or set(release) != {'schema', 'revision', 'bundle_sha256'}
                or type(release['schema']) is not int or release['schema'] != 1
                or not isinstance(release['revision'], str) or not re.fullmatch(r'[a-f0-9]{40}', release['revision'])
                or not isinstance(release['bundle_sha256'], str)
                or not re.fullmatch(r'[a-f0-9]{64}', release['bundle_sha256'])):
            raise ValueError('invalid release')
        return release
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError('invalid release') from error


def executor_approval(guard: Path, *, current: bool) -> dict:
    content = _private_file(guard/'executor-approval.json')
    allowed = guard/'approval.allowed_signers'
    signature = guard/'executor-approval.json.sig'
    _private_file(allowed)
    _private_file(signature)
    result = subprocess.run(['/usr/bin/ssh-keygen', '-Y', 'verify', '-f', str(allowed), '-I', 'owner',
                             '-n', 'logbook-repair-executor', '-s', str(signature)], input=content,
                            capture_output=True, timeout=10)
    if result.returncode:
        raise ValueError('executor approval signature invalid')
    try:
        approval = json.loads(content)
        if (not isinstance(approval, dict) or set(approval) != {'schema', 'revision', 'bundle_sha256', 'expires_at'}
                or type(approval['schema']) is not int or approval['schema'] != 1
                or not isinstance(approval['revision'], str) or not re.fullmatch(r'[a-f0-9]{40}', approval['revision'])
                or not isinstance(approval['bundle_sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', approval['bundle_sha256'])
                or type(approval['expires_at']) is not int
                or (current and approval['expires_at'] < int(time.time()))):
            raise ValueError('executor approval does not match package')
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError('invalid executor approval') from error
    return approval


def install_protected_executor(data: bytes, guard: Path) -> None:
    """Install an owner-approved fixed executor once, at its fixed location."""
    trusted_parent(guard/'executor')
    info = guard.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('private policy directory required')
    approval = executor_approval(guard, current=True)
    install_bundle(data, guard/'executor', approval['bundle_sha256'])


def verify_protected_executor(guard: Path) -> None:
    """Require the owner-approved executor and its identical fixed dispatcher."""
    def fail_walk(error: OSError) -> None:
        raise error
    root = guard/'executor'
    trusted_parent(root/'logbook'/'check')
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('private executor required')
    approval = executor_approval(guard, current=False)
    if _private_file(root/'.bundle-ready') != (approval['bundle_sha256']+'\n').encode():
        raise ValueError('executor approval mismatch')
    for current, directories, files in os.walk(root, followlinks=False, onerror=fail_walk):
        for name in directories + files:
            info = (Path(current)/name).lstat()
            if (stat.S_ISLNK(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022
                    or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))):
                raise ValueError('untrusted executor content')
    source = root/'logbook'
    for name in ('graph_maintenance_remote.py', 'graph_maintenance_recovery.py', 'graph_maintenance_worker.py'):
        _private_file(source/name)
    trusted_parent(PROTECTED_DISPATCHER)
    if _private_file(PROTECTED_DISPATCHER) != _private_file(source/'named_repair_dispatcher.py'):
        raise ValueError('protected dispatcher mismatch')


def install_named_bundle(data: bytes, destination: Path, staging: Path, guard: Path) -> None:
    """Install only a standing-policy-approved package at a constrained location."""
    trusted_parent(guard/'bundles'/'check')
    guard_info = guard.lstat()
    if not stat.S_ISDIR(guard_info.st_mode) or guard_info.st_uid != os.geteuid() or guard_info.st_mode & 0o077:
        raise ValueError('private policy directory required')
    parent = guard/'bundles'
    match = re.fullmatch(r'bundle-([a-f0-9]{32})', destination.name)
    if (not match or destination.parent != parent or staging.parent != parent
            or staging.name != 'window-'+match.group(1)):
        raise ValueError('named bundle paths rejected')
    allowed, signature = guard/'approval.allowed_signers', guard/'standing-policy.json.sig'
    content = _private_file(guard/'standing-policy.json')
    _private_file(allowed)
    _private_file(signature)
    result = subprocess.run(['/usr/bin/ssh-keygen','-Y','verify','-f',str(allowed),'-I','owner',
                             '-n','logbook-graph-policy','-s',str(signature)], input=content,
                            capture_output=True, timeout=10)
    if result.returncode:
        raise ValueError('standing policy signature invalid')
    try:
        policy = json.loads(content)
        if (not isinstance(policy, dict) or type(policy.get('schema')) is not int
                or policy['schema'] not in (1, 2)
                or set(policy) != ({'schema','revision','bundle_sha256','max_records','expires_at'}
                                   if policy['schema'] == 1 else {'schema','max_records','expires_at'})
                or type(policy['max_records']) is not int or not 1 <= policy['max_records'] <= 500
                or not ((policy['schema'] == 2 and policy['expires_at'] is None)
                        or (type(policy['expires_at']) is int and policy['expires_at'] >= int(time.time())))):
            raise ValueError('standing policy does not approve bundle')
        if policy['schema'] == 2:
            verify_protected_executor(guard)
        release = policy if policy['schema'] == 1 else approved_release(guard)
        if (not isinstance(release['revision'], str) or not re.fullmatch(r'[a-f0-9]{40}', release['revision'])
                or not isinstance(release['bundle_sha256'], str)
                or not re.fullmatch(r'[a-f0-9]{64}', release['bundle_sha256'])
                or hashlib.sha256(data).hexdigest() != release['bundle_sha256']):
            raise ValueError('standing policy does not approve bundle')
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError('invalid standing policy') from error
    staging.mkdir(mode=0o700)
    installed = False
    try:
        install_bundle(data,destination,release['bundle_sha256'])
        installed = True
        if policy['schema'] == 2:
            for name in ('release.json', 'release.json.sig'):
                content = _private_file(guard/name)
                fd = os.open(destination/name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as output:
                    output.write(content)
                    output.flush()
                    os.fsync(output.fileno())
            if approved_release(guard, destination) != release:
                raise ValueError('release changed during installation')
            sync_directory(destination)
        sync_directory(parent)
    except Exception:
        if installed:
            shutil.rmtree(destination)
        staging.rmdir()
        raise


def main() -> None:  # pragma: no cover
    if Path(sys.argv[0]).name == 'logbook-protected-executor-installer':
        if os.geteuid() != 0 or len(sys.argv) != 1:
            raise SystemExit('root and no arguments required')
        install_protected_executor(sys.stdin.buffer.read(30000001), Path('/var/lib/logbook-graph-guard'))
        print('EXECUTOR_INSTALLED')
        return
    if Path(sys.argv[0]).name == 'logbook-repair-bundle-installer':
        if os.geteuid() != 0 or len(sys.argv) != 3:
            raise SystemExit('root, named destination and staging required')
        install_named_bundle(sys.stdin.buffer.read(30000001), Path(sys.argv[1]), Path(sys.argv[2]),
                             Path('/var/lib/logbook-graph-guard'))
        print('BUNDLE_INSTALLED')
        return
    if os.geteuid() != 0 or len(sys.argv) != 4:
        raise SystemExit('root, destination, staging and digest required')
    destination, staging = Path(sys.argv[1]), Path(sys.argv[2])
    if staging.parent != destination.parent or staging == destination:
        raise SystemExit('bundle and staging must be distinct siblings')
    install_bundle(sys.stdin.buffer.read(30000001), destination, sys.argv[3])
    staging.mkdir(mode=0o700)
    sync_directory(staging.parent)
    print('BUNDLE_INSTALLED')


if __name__ == '__main__':  # pragma: no cover
    main()
