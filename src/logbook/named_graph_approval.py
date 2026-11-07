"""Short-lived, single-use approval for one named graph repair window."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import time
from pathlib import Path

from logbook.graph_maintenance_worker import private_directory, read_private_json

FIELDS = ('container_id', 'pid', 'port', 'unit', 'job_id', 'revision', 'bundle_sha256', 'plan_sha256')


def plan_digest(plan: dict) -> str:
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _check(request: dict, approval: dict, now: int) -> None:
    expected = {key: request.get(key) for key in FIELDS}
    if (not re.fullmatch(r'[a-f0-9]{64}', str(expected['container_id']))
            or not re.fullmatch(r'[a-f0-9]{40}', str(expected['revision']))
            or any(not re.fullmatch(r'[a-f0-9]{64}', str(expected[key]))
                   for key in ('bundle_sha256', 'plan_sha256'))
            or isinstance(expected['pid'], bool) or not isinstance(expected['pid'], int) or expected['pid'] < 1
            or type(expected['port']) is not int or expected['port'] != 7697 or isinstance(expected['job_id'], bool)
            or not isinstance(expected['job_id'], int) or expected['job_id'] < 1
            or not isinstance(expected['unit'], str)
            or not re.fullmatch(r'logbook-graph-maintenance-[a-f0-9]{32}\.service', expected['unit'])):
        raise ValueError('invalid approval target')
    if set(approval) != {'schema', 'approved_at', 'expires_at', *FIELDS} or type(approval.get('schema')) is not int or approval.get('schema') != 1:
        raise ValueError('invalid approval record')
    if any(approval.get(key) != value for key, value in expected.items()):
        raise ValueError('approval target mismatch')
    issued, expires = approval.get('approved_at'), approval.get('expires_at')
    if (isinstance(issued, bool) or isinstance(expires, bool) or not isinstance(issued, int)
            or not isinstance(expires, int) or not 0 < expires-issued <= 300
            or not issued <= now <= expires):
        raise ValueError('approval expired or invalid')


def _sync(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _private_bytes(path: Path) -> bytes:
    private_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or info.st_size > 100000):
            raise ValueError('private approval file required')
        return source.read()


def verify_owner_signature(approval: Path, signature: Path, guard: Path) -> None:
    """Verify exact approval bytes against a separately installed owner public key."""
    allowed = guard/'approval.allowed_signers'
    _private_bytes(allowed)
    _private_bytes(signature)
    content = _private_bytes(approval)
    result = subprocess.run(['/usr/bin/ssh-keygen', '-Y', 'verify', '-f', str(allowed),
                             '-I', 'owner', '-n', 'logbook-graph-repair', '-s', str(signature)],
                            input=content, capture_output=True, timeout=10)
    if result.returncode != 0:
        raise ValueError('owner approval signature invalid')


def consume_named_approval(request: dict, plan: dict, guard: Path, stage: Path, *, now: int | None = None) -> None:
    private_directory(guard)
    private_directory(stage)
    if plan_digest(plan) != request.get('plan_sha256'):
        raise ValueError('approved plan mismatch')
    source, pending, consumed = guard/'approval.json', guard/'approval.consuming', stage/'approval.consumed'
    approval = read_private_json(source)
    _check(request, approval, int(time.time()) if now is None else now)
    if Path(request.get('boot_guard_dir', '')) != guard:
        raise ValueError('approval guard mismatch')
    signature, pending_signature = guard/'approval.json.sig', guard/'approval.consuming.sig'
    verify_owner_signature(source, signature, guard)
    os.link(source, pending, follow_symlinks=False)
    os.link(signature, pending_signature, follow_symlinks=False)
    _sync(guard)
    source.unlink()
    signature.unlink()
    _sync(guard)
    os.link(pending, consumed, follow_symlinks=False)
    os.link(pending_signature, stage/'approval.consumed.sig', follow_symlinks=False)
    _sync(stage)
    pending.unlink()
    pending_signature.unlink()
    _sync(guard)


def validate_consumed_approval(request: dict, stage: Path, *, now: int | None = None) -> None:
    _check(request, read_private_json(stage/'approval.consumed'), int(time.time()) if now is None else now)
    if plan_digest(read_private_json(stage/'plan.json')) != request.get('plan_sha256'):
        raise ValueError('approved plan mismatch')
    verify_owner_signature(stage/'approval.consumed', stage/'approval.consumed.sig',
                           Path(request['boot_guard_dir']))


def verify_bundle_marker(request: dict, bundle: Path) -> None:
    private_directory(bundle)
    if Path(request['recovery_file']) != bundle/'logbook/graph_maintenance_recovery.py':
        raise ValueError('bundle path mismatch')
    fd = os.open(bundle/'.bundle-ready', os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'r', encoding='utf-8') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o022 or source.read(80) != request.get('bundle_sha256', '')+'\n'):
            raise ValueError('bundle marker mismatch')
