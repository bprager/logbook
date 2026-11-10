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
ACTIONS = {'named-check','named-stage','named-preflight','named-launch','named-status','named-outcome','named-offline','named-authorize'}
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


def protected_executor() -> Path:
    """Validate a fixed, root-private executor tree, never a release path."""
    def fail_walk(error: OSError) -> None:
        raise error
    root = GUARD/'executor'
    trusted_path(root, directory=True, private=True)
    for current, directories, files in os.walk(root, followlinks=False, onerror=fail_walk):
        for name in directories + files:
            path = Path(current)/name
            info = path.lstat()
            if (stat.S_ISLNK(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022
                    or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))):
                raise ValueError('untrusted executor content')
    package = root/'logbook'
    for name in ('graph_maintenance_remote.py', 'graph_maintenance_recovery.py', 'graph_maintenance_worker.py'):
        trusted_path(package/name, directory=False, private=True)
    return package/'graph_maintenance_remote.py'


def validate_request_shape(action: str, request: dict, *, protected: bool = False) -> Path:
    if action not in ACTIONS or not isinstance(request,dict):
        raise ValueError('unsupported repair action')
    expected = FIELDS | ({'plan_data','plan_proof'} if action in {'named-check','named-stage'} else set())
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
    executor = GUARD/'executor' if protected else bundle
    expected_paths = {'private_dir':stage, 'journal':GUARD/'journals'/(token+'.json'),
                      'worker':stage/'worker', 'recovery_worker':stage/'restore',
                      'recovery_file':executor/'logbook/graph_maintenance_recovery.py'}
    if any(request[k] != str(value) for k,value in expected_paths.items()):
        raise ValueError('repair paths outside allowed window')
    return executor/'logbook/graph_maintenance_remote.py'


def validate_request(action: str, request: dict, *, protected: bool = False) -> Path:
    entry = validate_request_shape(action, request, protected=protected)
    token = request['unit'].removeprefix('logbook-graph-maintenance-').removesuffix('.service')
    bundle = GUARD/'bundles'/('bundle-'+token)
    stage = Path(request['private_dir'])
    for path in (GUARD,bundle,stage,GUARD/'journals'):
        trusted_path(path,directory=True,private=True)
    if protected:
        entry = protected_executor()
    for path in (entry,bundle/'.bundle-ready'):
        trusted_path(path,directory=False,private=True)
    if (bundle/'.bundle-ready').read_text() != request['bundle_sha256']+'\n':
        raise ValueError('bundle marker mismatch')
    return entry


def reject_uncontained_policy(*, protected: bool = False) -> None:
    """Do not execute a preinstalled separately authorized release as root."""
    policy = GUARD/'standing-policy.json'
    if not os.path.lexists(policy):
        # Recovery of a previously installed original-format window must remain
        # possible if the owner removes its policy during an incident.
        return
    trusted_path(policy, directory=False, private=True)
    with policy.open('rb') as source:
        content = source.read(100001)
    if len(content) > 100000:
        raise ValueError('policy too large')
    document = json.loads(content)
    if (not isinstance(document, dict) or type(document.get('schema')) is not int
            or document['schema'] != (2 if protected else 1)):
        raise ValueError('separate release authority is disabled')


def main() -> None:  # pragma: no cover
    if os.geteuid() != 0 or len(sys.argv) != 2:
        raise SystemExit('root and one named action required')
    raw = sys.stdin.buffer.read(5000001)
    if len(raw) > 5000000:
        raise SystemExit('repair request too large')
    request = json.loads(raw)
    if sys.argv[1] == 'named-discover':
        if (len(raw) > 4096 or not isinstance(request,dict) or set(request) != {'job_ids'}
                or not isinstance(request['job_ids'],list) or not 1 <= len(request['job_ids']) <= 100
                or any(type(value) is not int or not 1 <= value <= 9223372036854775807 for value in request['job_ids'])
                or request['job_ids'] != sorted(set(request['job_ids']))):
            raise ValueError('bounded job IDs required')
        entry = protected_executor().parent/'graph_repair_discovery.py'
        trusted_path(entry,directory=False,private=True)
        result = subprocess.run(['/usr/bin/python3','-I',str(entry)],input=raw,
                                env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8'},
                                cwd='/',timeout=60,check=False)
        raise SystemExit(result.returncode)
    protected = (isinstance(request, dict) and request.get('recovery_file')
                 == str(GUARD/'executor'/'logbook'/'graph_maintenance_recovery.py'))
    reject_uncontained_policy(protected=protected)
    entry = validate_request(sys.argv[1],request,protected=protected)
    result = subprocess.run(['/usr/bin/python3','-I',str(entry),sys.argv[1]],input=raw,
                            env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8'},
                            cwd='/',timeout=240,check=False)
    raise SystemExit(result.returncode)


if __name__ == '__main__':  # pragma: no cover
    main()
