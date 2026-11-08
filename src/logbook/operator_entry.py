"""Fixture operator and bounded recovery of an already staged named graph window."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

from logbook.config import AppConfig, load_app_config
from logbook.graph_maintenance_cross_host import NamedGraphMaintenance, SshMaintenance, coordinate_leased_window
from logbook.graph_maintenance_launcher import with_logbook_lock_descriptors
from logbook.processing_restart_guard import clear_guard
from logbook.fixture_recovery_release import _validate_named
from logbook.graph_maintenance_recovery import DockerRecovery
from logbook.graph_maintenance_worker import private_directory, read_private_json
from logbook.operator_bundle import build_revision_bundle, provision_bundle, provision_named_bundle, verify_checkout
from logbook.operator_window import stage_plan_and_run
from logbook.operator_plan import prepare_job_plan
from logbook.named_graph_approval import FIELDS, _check, plan_digest


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



def prepare_named(config: AppConfig, job_id: int, options: dict, state: Path) -> str:
    """Prepare one bounded repair and its reviewable target; do not launch it."""
    private_directory(state.parent)
    token = uuid.uuid4().hex
    parent = Path(options['remote_parent'])
    bundle, staging = parent/f'bundle-{token}', parent/f'window-{token}'
    unit = 'logbook-graph-maintenance-'+token+'.service'
    DockerRecovery(options['container_id'], options['pid'], 7697, unit)
    revision = options['revision']
    verify_checkout(Path(__file__).resolve().parents[2], revision)
    payload = build_release_bundle(revision, Path(options['wheel_dir']))
    with tempfile.TemporaryDirectory(dir=state.parent) as temporary:
        plan_path = Path(temporary)/'plan.json'
        prepare_job_plan(config, job_id, plan_path)
        plan = read_private_json(plan_path)
    request = {'profile':'named-graph', 'container_id':options['container_id'], 'pid':options['pid'],
               'port':7697, 'unit':unit, 'private_dir':str(staging),
               'journal':str(Path(options['journal_dir'])/f'{token}.json'),
               'worker':str(staging/'worker'), 'recovery_file':str(bundle/'logbook/graph_maintenance_recovery.py'),
               'recovery_worker':str(staging/'restore'), 'seconds':options['seconds'],
               'boot_guard_dir':'/var/lib/logbook-graph-guard', 'boot_guard_service':'memgraph.service',
               'job_id':job_id, 'revision':revision, 'bundle_sha256':hashlib.sha256(payload).hexdigest(),
               'plan_sha256':plan_digest(plan)}
    entrypoint = bundle/'logbook/graph_maintenance_remote.py'
    saved = {'host':options['host'], 'identity':options['identity'], 'entrypoint':str(entrypoint),
             'request':request, 'plan':plan}
    encoded = json.dumps(saved, sort_keys=True).encode()
    if len(encoded) > 5000000:
        raise ValueError('private state exceeds limit')
    fd = os.open(state, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(encoded)
        output.flush()
        os.fsync(output.fileno())
    directory = os.open(state.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    provision_named_bundle(options['host'], Path(options['identity']), bundle, staging, payload)
    return 'prepared'


def run_named(config: AppConfig, state: Path) -> str:
    saved = read_private_json(state)
    remote = _named_remote(state)
    plan = saved['plan']
    if not isinstance(plan, dict) or plan_digest(plan) != remote.request.get('plan_sha256'):
        raise ValueError('prepared plan mismatch')
    coordinate_leased_window(config, remote, timeout_seconds=remote.request['seconds']+270, stage_plan=plan)
    return remote.outcome()


def prepare_owner_approval(state: Path, destination: Path) -> str:
    """Prepare a short-lived request for the owner to review and sign."""
    saved = read_private_json(state)
    remote = _named_remote(state)
    request = remote.request
    plan = saved['plan']
    if not isinstance(plan, dict) or plan_digest(plan) != request.get('plan_sha256'):
        raise ValueError('prepared plan mismatch')
    private_directory(destination.parent)
    now = int(time.time())
    approval = {'schema':1, **{key:request[key] for key in FIELDS}, 'approved_at':now, 'expires_at':now+300}
    _check(request, approval, now)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as output:
        json.dump(approval, output, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    directory = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return 'approval-prepared'

def release_processing_guard(config: AppConfig, remote: SshMaintenance) -> None:
    """Clear a matching fixture guard only under both locks and after confirmation."""
    request = remote.request
    if request.get('sandbox_only') is not True:
        raise ValueError('fixture state required')
    unit = request.get('unit')
    if not isinstance(unit, str):
        raise ValueError('maintenance unit required')

    def release(_pipeline: int, _graph: int) -> None:
        if not remote.recovered():
            raise RuntimeError('fixture recovery unconfirmed')
        remote.outcome()
        clear_guard(config.processing_root, unit)

    with_logbook_lock_descriptors(config, release, allow_pending=True)


def release_fixture_guard(config: AppConfig, state: Path) -> str:
    saved = read_private_json(state)
    remote = SshMaintenance(saved['host'], Path(saved['identity']), Path(saved['entrypoint']), saved['request'])
    release_processing_guard(config, remote)
    return 'released'


def _named_remote(state: Path) -> NamedGraphMaintenance:
    saved = read_private_json(state)
    request = saved['request']
    if not isinstance(request, dict) or request.get('profile') != 'named-graph' or request.get('sandbox_only') is True:
        raise ValueError('named graph recovery state required')
    _validate_named(request)
    return NamedGraphMaintenance(saved['host'], Path(saved['identity']), Path(saved['entrypoint']), request)


def named_graph_recovery(state: Path, action: str) -> str:
    remote = _named_remote(state)
    if action == 'offline':
        result = remote.invoke('named-offline')
        if result not in {'rolled-back', 'not-retained', 'retained'}:
            raise RuntimeError('offline recovery outcome unconfirmed')
        return result
    if action == 'authorize':
        if remote.invoke('named-authorize') != 'STARTED':
            raise RuntimeError('named graph restart unconfirmed')
        return 'started'
    if action == 'status':
        return remote.outcome() if remote.recovered() else 'pending'
    raise ValueError('expected offline, authorize or status')


def release_named_processing_guard(config: AppConfig, remote: NamedGraphMaintenance) -> None:
    request = remote.request
    if request.get('profile') != 'named-graph' or request.get('sandbox_only') is True:
        raise ValueError('named graph recovery state required')
    _validate_named(request)
    unit = request.get('unit')
    if not isinstance(unit, str):
        raise ValueError('maintenance unit required')

    def release(_pipeline: int, _graph: int) -> None:
        if not remote.recovered():
            raise RuntimeError('named graph recovery unconfirmed')
        remote.outcome()
        clear_guard(config.processing_root, unit)

    with_logbook_lock_descriptors(config, release, allow_pending=True)


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
        if result not in {'rolled-back', 'not-retained', 'retained'}:
            raise RuntimeError('offline recovery outcome unconfirmed')
        return result
    if action == 'status':
        return remote.outcome() if remote.recovered() else 'pending'
    raise ValueError('expected retry, status, offline or authorize')


if __name__ == '__main__':  # pragma: no cover
    if len(sys.argv) == 4 and sys.argv[1] == 'named-approval':
        print(prepare_owner_approval(Path(sys.argv[2]), Path(sys.argv[3])))
    elif len(sys.argv) == 6 and sys.argv[1] == 'named-prepare':
        print(prepare_named(load_app_config(Path(sys.argv[2])), int(sys.argv[3]),
                            read_private_json(Path(sys.argv[4])), Path(sys.argv[5])))
    elif len(sys.argv) == 4 and sys.argv[1] == 'named-run':
        print(run_named(load_app_config(Path(sys.argv[2])), Path(sys.argv[3])))
    elif len(sys.argv) == 4 and sys.argv[1] == 'named-release':
        release_named_processing_guard(load_app_config(Path(sys.argv[2])), _named_remote(Path(sys.argv[3])))
        print('released')
    elif len(sys.argv) == 3 and sys.argv[1] in {'named-offline', 'named-authorize', 'named-status'}:
        print(named_graph_recovery(Path(sys.argv[2]), sys.argv[1].removeprefix('named-')))
    elif len(sys.argv) == 4 and sys.argv[1] == 'release':
        print(release_fixture_guard(load_app_config(Path(sys.argv[2])), Path(sys.argv[3])))
    elif len(sys.argv) == 3 and sys.argv[1] in {'retry', 'status', 'offline', 'authorize'}:
        print(fixture_recovery(Path(sys.argv[2]), sys.argv[1]))
    elif len(sys.argv) == 5:
        print(run_fixture(load_app_config(Path(sys.argv[1])), int(sys.argv[2]),
                          read_private_json(Path(sys.argv[3])), Path(sys.argv[4])))
    else:
        raise SystemExit('usage: operator_entry ENV JOB_ID PRIVATE_OPTIONS PRIVATE_STATE | retry|status|offline|authorize PRIVATE_STATE | named-offline|named-authorize|named-status PRIVATE_STATE | named-release ENV PRIVATE_STATE')
