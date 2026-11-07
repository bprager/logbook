"""Release fixture startup and locks only after target-bound offline recovery."""
from __future__ import annotations

import csv
import io
import json
import os
import re
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from logbook.fixture_boot_guard import (FixtureBootGuard, MemgraphBootGuard, verify_fixture_binding,
                                        verify_memgraph_binding)
from logbook.fixture_offline_recovery import boot_id
from logbook.graph_maintenance_worker import completion_matches, read_private_json


NAMED_GUARD_DIR = Path('/var/lib/logbook-graph-guard')
NAMED_VOLUME = 'memgraph-data-v313-20261011'


def _validate_named(request: dict) -> None:
    if (request.get('boot_guard_service') != 'memgraph.service'
            or Path(request.get('boot_guard_dir', '')) != NAMED_GUARD_DIR
            or request.get('port') != 7697):
        raise ValueError('named graph target mismatch')


def _volume_valid(volume: str, named: bool) -> bool:
    return volume == NAMED_VOLUME if named else bool(re.fullmatch(r'logbook-fixture-[a-z0-9-]+', volume))


def verify_offline_receipt(request: dict, *, _named: bool = False) -> dict:
    if _named:
        _validate_named(request)
    directory = Path(request['private_dir'])
    record = read_private_json(directory/'boot-target.json')
    result = read_private_json(directory/'offline-outcome.json')
    journal = Path(request['journal'])
    batch_record = read_private_json(journal)
    batch = batch_record.get('batch', '')
    expected = {'container':request['container_id'], 'pid':request['pid'], 'unit':request['unit'],
                'service':request['boot_guard_service'], 'guard_dir':request['boot_guard_dir'],
                'journal':request['journal'], 'port':request['port'], 'recovery_file':request['recovery_file']}
    volume, image = record.get('volume', ''), record.get('image', '')
    if (any(record.get(k) != v for k, v in expected.items()) or not record.get('boot_id')
            or record['boot_id'] == boot_id() or not _volume_valid(volume, _named)
            or not re.fullmatch(r'sha256:[a-f0-9]{64}', image) or batch_record.get('schema') != 1
            or str(uuid.UUID(batch)) != batch):
        raise ValueError('invalid offline recovery target')
    outcome = result.get('outcome')
    completed = os.path.lexists(journal.with_name(journal.name+'.complete'))
    if (completed != (outcome == 'retained')
            or (completed and not completion_matches(journal, request, batch))):
        raise ValueError('invalid offline completion')
    if (outcome not in {'rolled-back', 'not-retained', 'retained'} or result != {
            **expected, 'volume':volume, 'boot_id':boot_id(), 'outcome':outcome, 'batch':batch}):
        raise ValueError('invalid offline recovery receipt')
    return {'volume':volume, 'image':image, 'outcome':outcome}


def fixture_restarted(request: dict, recovery, *, _named: bool = False) -> bool:
    record = verify_offline_receipt(request, _named=True) if _named else verify_offline_receipt(request)
    service = request['boot_guard_service']
    guard = (MemgraphBootGuard(Path(request['boot_guard_dir']), recovery.run) if _named
             else FixtureBootGuard(Path(request['boot_guard_dir']), service, recovery.run))
    guard.verify()
    guard._check_marker()
    name = 'memgraph-service' if _named else service.removesuffix('.service')
    detail = recovery.run(['docker', 'inspect', '--format',
                           '{{.Id}} {{.State.Pid}} {{.State.Running}} {{.Image}}', name]).split()
    if (len(detail) != 4 or not re.fullmatch(r'[a-f0-9]{64}', detail[0]) or not detail[1].isdigit()
            or detail[2:] != ['true', record['image']]):
        raise ValueError('restarted fixture identity invalid')
    target = SimpleNamespace(container_id=detail[0], pid=int(detail[1]), run=recovery.run)
    if _named:
        verify_memgraph_binding(target)
    else:
        verify_fixture_binding(target, service)
    mounts = json.loads(recovery.run(['docker', 'inspect', '--format', '{{json .Mounts}}', detail[0]]))
    matches = [m for m in mounts if m.get('Destination') == '/var/lib/memgraph']
    if (len(matches) != 1 or matches[0].get('Type') != 'volume'
            or matches[0].get('Name') != record['volume'] or not matches[0].get('RW')):
        raise ValueError('restarted fixture volume invalid')
    consumers = recovery.run(['docker', 'ps', '--no-trunc', '-q', '--filter',
                              'volume='+record['volume']]).split()
    if consumers != [detail[0]]:
        raise ValueError('unexpected fixture volume consumers')
    output = recovery.run(['docker', 'exec', '-i', name, 'mgconsole', '--host=127.0.0.1',
                           '--port='+str(request['port']), '--output_format=csv'], 'RETURN 1;\n')
    if list(csv.reader(io.StringIO(output))) != [['1'], ['1']]:
        raise ValueError('restarted fixture graph is not ready')
    return True


def authorize_fixture_restart(request: dict, recovery, *, _named: bool = False) -> None:
    record = verify_offline_receipt(request, _named=True) if _named else verify_offline_receipt(request)
    guard = (MemgraphBootGuard(Path(request['boot_guard_dir']), recovery.run) if _named
             else FixtureBootGuard(Path(request['boot_guard_dir']), request['boot_guard_service'], recovery.run))
    state = recovery.run(['systemctl', 'show', request['boot_guard_service'], '-p', 'ActiveState', '--value']).strip()
    if state == 'active':
        (fixture_restarted(request, recovery, _named=True) if _named else fixture_restarted(request, recovery))
        return
    guard.verify('inactive')
    if recovery.run(['docker', 'ps', '-q', '--filter', 'volume='+record['volume']]).strip():
        raise ValueError('fixture volume is in use')
    guard.allow('inactive')
    recovery.run(['systemctl', 'start', request['boot_guard_service']], timeout=45)
    for attempt in range(30):
        try:
            (fixture_restarted(request, recovery, _named=True) if _named else fixture_restarted(request, recovery))
            return
        except Exception:
            if attempt == 29:
                raise
        time.sleep(1)


def verify_named_graph_receipt(request: dict) -> dict:
    return verify_offline_receipt(request, _named=True)


def authorize_named_graph_restart(request: dict, recovery) -> None:
    """Reopen only a verified named graph target; not exposed by the operator."""
    authorize_fixture_restart(request, recovery, _named=True)
