#!/usr/bin/python3
"""Candidate fixed root dispatcher for a named Logbook graph window."""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

GUARD = Path('/var/lib/logbook-graph-guard')
ACTIONS = {'named-stage','named-preflight','named-launch','named-status','named-outcome','named-offline','named-authorize'}
FIELDS = {'profile','boot_guard_service','boot_guard_dir','port','unit','private_dir','journal','worker',
          'recovery_worker','recovery_file','container_id','pid','seconds','job_id','revision','bundle_sha256','plan_sha256'}


def trusted_path(path: Path, *, directory: bool, private: bool = False) -> None:
    """Reject symlinks and files writable by non-root along the full path."""
    for current in (path, *path.parents):
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError('untrusted path')
    info = path.lstat()
    if ((directory and not stat.S_ISDIR(info.st_mode))
            or (not directory and not stat.S_ISREG(info.st_mode))
            or (private and info.st_mode & 0o077)):
        raise ValueError('unexpected trusted path type')


def validate_request_shape(action: str, request: dict) -> Path:
    if action not in ACTIONS or not isinstance(request,dict):
        raise ValueError('unsupported repair action')
    expected = FIELDS | ({'plan_data','plan_proof'} if action == 'named-stage' else set())
    if set(request) != expected:
        raise ValueError('unexpected repair request fields')
    unit = request['unit']
    match = re.fullmatch(r'logbook-graph-maintenance-([a-f0-9]{32})\.service',unit) if isinstance(unit,str) else None
    if (not match or request['profile'] != 'named-graph' or request['boot_guard_service'] != 'memgraph.service'
            or request['boot_guard_dir'] != str(GUARD) or type(request['port']) is not int or request['port'] != 7697
            or type(request['pid']) is not int or request['pid'] < 1
            or type(request['job_id']) is not int or request['job_id'] < 1
            or type(request['seconds']) is not int or not 10 <= request['seconds'] <= 3600
            or not isinstance(request['container_id'],str) or not re.fullmatch(r'[a-f0-9]{64}',request['container_id'])
            or not isinstance(request['revision'],str) or not re.fullmatch(r'[a-f0-9]{40}',request['revision'])
            or any(not isinstance(request[k],str) or not re.fullmatch(r'[a-f0-9]{64}',request[k])
                   for k in ('bundle_sha256','plan_sha256'))):
        raise ValueError('invalid repair target')
    token = match.group(1)
    bundle = GUARD/'bundles'/('bundle-'+token)
    stage = GUARD/'bundles'/('window-'+token)
    expected_paths = {'private_dir':stage, 'journal':GUARD/'journals'/(token+'.json'),
                      'worker':stage/'worker', 'recovery_worker':stage/'restore',
                      'recovery_file':bundle/'logbook/graph_maintenance_recovery.py'}
    if any(request[k] != str(value) for k,value in expected_paths.items()):
        raise ValueError('repair paths outside allowed window')
    return bundle/'logbook/graph_maintenance_remote.py'


def validate_request(action: str, request: dict) -> Path:
    entry = validate_request_shape(action, request)
    bundle = entry.parent.parent
    stage = Path(request['private_dir'])
    for path in (GUARD,bundle,stage,GUARD/'journals'):
        trusted_path(path,directory=True,private=True)
    for path in (entry,bundle/'.bundle-ready'):
        trusted_path(path,directory=False,private=True)
    if (bundle/'.bundle-ready').read_text() != request['bundle_sha256']+'\n':
        raise ValueError('bundle marker mismatch')
    return entry


def main() -> None:  # pragma: no cover
    if os.geteuid() != 0 or len(sys.argv) != 2:
        raise SystemExit('root and one named action required')
    raw = sys.stdin.buffer.read(5000001)
    if len(raw) > 5000000:
        raise SystemExit('repair request too large')
    request = json.loads(raw)
    entry = validate_request(sys.argv[1],request)
    result = subprocess.run(['/usr/bin/python3','-I',str(entry),sys.argv[1]],input=raw,
                            env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8'},
                            cwd='/',timeout=240,check=False)
    raise SystemExit(result.returncode)


if __name__ == '__main__':  # pragma: no cover
    main()
