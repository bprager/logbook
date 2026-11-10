"""One bounded automatic repair attempt using existing protected components."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import uuid
from pathlib import Path

from logbook.graph_maintenance_worker import private_directory, read_private_json
from logbook.lock_guard_client import OPERATOR_KEY, _check_remote, release_named_handoff
from logbook.operator_entry import _named_remote, prepare_named, read_release_bundle, run_named
from logbook.plan_producer import request_job_ids
from logbook.repair_discovery_client import discover_missing_jobs


def _cursor(root: Path) -> int:
    path = root/'repair-cursor.json'
    if not os.path.lexists(path):
        return 0
    value = read_private_json(path)
    if (set(value) != {'after'} or type(value['after']) is not int
            or not 0 <= value['after'] <= 9223372036854775807):
        raise ValueError('invalid repair cursor')
    return value['after']


def _save_json(root: Path, name: str, value: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix='.'+name+'-',dir=root)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as output:
            json.dump(value,output,sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary,root/name)
        directory = os.open(root,os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _clear_pending(root: Path) -> None:
    (root/'repair-pending.json').unlink()
    fd = os.open(root,os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _recover_pending(root: Path) -> str | None:
    path = root/'repair-pending.json'
    if not os.path.lexists(path):
        return None
    pending = read_private_json(path)
    if (set(pending) != {'phase','state'} or pending['phase'] not in {'preparing','running'}
            or not isinstance(pending['state'],str)
            or not re.fullmatch(r'repair-[a-f0-9]{32}\.json',pending['state'])):
        raise ValueError('invalid pending repair')
    if pending['phase'] == 'preparing':
        _clear_pending(root)
        return 'preparation-interrupted'
    remote = _named_remote(root/pending['state'])
    _check_remote(remote)
    if not remote.recovered():
        if not remote.cancel_unstarted() or not remote.recovered():
            return 'recovery-pending'
    outcome = remote.outcome()
    release_named_handoff(remote)
    _clear_pending(root)
    return outcome


def _release(root: Path) -> dict:
    value = read_private_json(root/'current.json')
    if (set(value) != {'schema','sequence','revision','bundle_sha256','bundle_path'}
            or type(value['schema']) is not int or value['schema'] != 2
            or type(value['sequence']) is not int or not 1 <= value['sequence'] <= 9223372036854775807
            or not isinstance(value['revision'],str) or not re.fullmatch(r'[a-f0-9]{40}',value['revision'])
            or not isinstance(value['bundle_sha256'],str) or not re.fullmatch(r'[a-f0-9]{64}',value['bundle_sha256'])
            or value['bundle_path'] != str(root/(value['bundle_sha256']+'.tar.gz'))):
        raise ValueError('invalid current repair release')
    if hashlib.sha256(read_release_bundle(Path(value['bundle_path']))).hexdigest() != value['bundle_sha256']:
        raise ValueError('repair release package mismatch')
    return value


def repair_cycle(root: Path) -> str:
    """Inspect one page and repair at most one missing job; never prune data."""
    private_directory(root)
    fd = os.open(root/'repair-cycle.lock',os.O_RDWR | os.O_CREAT | getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'r+b') as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('private repair cycle lock required')
        fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
        pending = _recover_pending(root)
        if pending is not None:
            return pending
        after = _cursor(root)
        ids = request_job_ids(after,100)
        if not ids:
            _save_json(root,'repair-cursor.json',{'after':0})
            return 'scan-complete'
        found = discover_missing_jobs(ids)
        missing = found['missing_job_ids']
        if not missing:
            _save_json(root,'repair-cursor.json',{'after':ids[-1] if len(ids) == 100 else 0})
            return 'page-complete' if len(ids) == 100 else 'scan-complete'
        release = _release(root)
        state = root/('repair-'+uuid.uuid4().hex+'.json')
        options = {'remote_parent':'/var/lib/logbook-graph-guard/bundles',
                   'journal_dir':'/var/lib/logbook-graph-guard/journals',
                   'container_id':found['container_id'], 'pid':found['pid'], 'seconds':120,
                   'revision':release['revision'], 'release_bundle':release['bundle_path'],
                   'protected_executor':True, 'host':'logbook-repair@odin', 'identity':str(OPERATOR_KEY)}
        _save_json(root,'repair-pending.json',{'phase':'preparing','state':state.name})
        if prepare_named(None,missing[0],options,state) != 'prepared':
            raise RuntimeError('repair preparation failed')
        _save_json(root,'repair-pending.json',{'phase':'running','state':state.name})
        outcome = run_named(None,state,restricted=True)
        _clear_pending(root)
        if outcome != 'retained':
            raise RuntimeError('repair was not retained')
        return outcome


def main() -> None:  # pragma: no cover
    if len(sys.argv) != 2 or sys.argv[1] != 'repair-cycle':
        raise SystemExit('fixed repair cycle required')
    print('REPAIR_CYCLE',repair_cycle(OPERATOR_KEY.parent))
