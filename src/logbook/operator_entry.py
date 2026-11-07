"""Fixture-only operator entry point for the experimental maintenance path."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

from logbook.config import AppConfig, load_app_config
from logbook.graph_maintenance_cross_host import SshMaintenance
from logbook.graph_maintenance_recovery import DockerRecovery
from logbook.graph_maintenance_worker import private_directory, read_private_json
from logbook.operator_bundle import build_revision_bundle, provision_bundle, verify_checkout
from logbook.operator_window import stage_plan_and_run


def build_release_bundle(revision: str, wheel_dir: Path) -> bytes:
    return build_revision_bundle(Path(__file__).resolve().parents[2], revision, wheel_dir)


def run_fixture(config: AppConfig, job_id: int, options: dict, state: Path) -> str:
    private_directory(state.parent)
    token = uuid.uuid4().hex
    parent = Path(options['remote_parent'])
    bundle, staging = parent/f'bundle-{token}', parent/f'window-{token}'
    journal = Path(options['journal_dir'])/f'{token}.json'
    unit = 'logbook-graph-maintenance-'+token+'.service'
    DockerRecovery(options['container_id'], options['pid'], options['port'], unit)
    request = {'container_id':options['container_id'], 'pid':options['pid'], 'port':options['port'],
               'unit':unit, 'private_dir':str(staging), 'journal':str(journal),
               'worker':str(staging/'worker'), 'recovery_file':str(bundle/'logbook/graph_maintenance_recovery.py'),
               'recovery_worker':str(staging/'restore'), 'seconds':options['seconds'], 'sandbox_only':True,
               'boot_guard_dir':options['boot_guard_dir'], 'boot_guard_service':options['boot_guard_service']}
    entrypoint = bundle/'logbook/graph_maintenance_remote.py'
    revision = options['revision']
    verify_checkout(Path(__file__).resolve().parents[2], revision)
    payload = build_release_bundle(revision, Path(options['wheel_dir']))
    saved = {'host':options['host'], 'identity':options['identity'], 'entrypoint':str(entrypoint),
             'revision':revision, 'bundle_sha256':hashlib.sha256(payload).hexdigest(), 'request':request}
    fd = os.open(state, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as output:
        json.dump(saved, output, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    directory = os.open(state.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    provision_bundle(options['host'], Path(options['identity']), bundle, staging, payload)
    remote = SshMaintenance(options['host'], Path(options['identity']), entrypoint, request)
    return stage_plan_and_run(config, job_id, remote, timeout_seconds=options['seconds']+270)


def fixture_recovery(state: Path, action: str) -> str:
    saved = read_private_json(state)
    request = saved['request']
    if not isinstance(request, dict) or request.get('sandbox_only') is not True:
        raise ValueError('fixture state required')
    remote = SshMaintenance(saved['host'], Path(saved['identity']), Path(saved['entrypoint']), request)
    if action == 'retry':
        remote.retry()
        return 'retry-started'
    if action == 'authorize':
        if remote.invoke('authorize') != 'STARTED':
            raise RuntimeError('fixture restart unconfirmed')
        return 'started'
    if action == 'offline':
        result = remote.invoke('offline')
        if result not in {'rolled-back', 'not-retained'}:
            raise RuntimeError('offline recovery outcome unconfirmed')
        return result
    if action == 'status':
        return remote.outcome() if remote.recovered() else 'pending'
    raise ValueError('expected retry, status, offline or authorize')


if __name__ == '__main__':  # pragma: no cover
    if len(sys.argv) == 3 and sys.argv[1] in {'retry', 'status', 'offline', 'authorize'}:
        print(fixture_recovery(Path(sys.argv[2]), sys.argv[1]))
    elif len(sys.argv) == 5:
        print(run_fixture(load_app_config(Path(sys.argv[1])), int(sys.argv[2]),
                          read_private_json(Path(sys.argv[3])), Path(sys.argv[4])))
    else:
        raise SystemExit('usage: operator_entry ENV JOB_ID PRIVATE_OPTIONS PRIVATE_STATE | retry|status|offline|authorize PRIVATE_STATE')
