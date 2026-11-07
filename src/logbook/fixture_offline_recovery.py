"""Fixture-only recovery after the protected graph host has restarted."""
from __future__ import annotations

import fcntl
import json
import os
import re
import uuid
from pathlib import Path

from logbook.fixture_boot_guard import FixtureBootGuard
from logbook.graph_maintenance_worker import private_directory, read_private_json


def boot_id() -> str:
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def _save(path: Path, value: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as output:
        json.dump(value, output, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def record_fixture_storage(request: dict, recovery) -> None:
    directory = Path(request['private_dir'])
    private_directory(directory)
    mounts = json.loads(recovery.run(['docker', 'inspect', '--format', '{{json .Mounts}}', recovery.container_id]))
    candidates = [m for m in mounts if m.get('Destination') == '/var/lib/memgraph']
    if (len(candidates) != 1 or candidates[0].get('Type') != 'volume' or not candidates[0].get('RW')
            or not re.fullmatch(r'logbook-fixture-[a-z0-9-]+', candidates[0].get('Name', ''))):
        raise ValueError('dedicated fixture volume required')
    image = recovery.run(['docker', 'inspect', '--format', '{{.Image}}', recovery.container_id]).strip()
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', image):
        raise ValueError('fixture image ID required')
    _save(directory/'boot-target.json', {
        'container':recovery.container_id, 'pid':recovery.pid, 'unit':request['unit'],
        'service':request['boot_guard_service'], 'guard_dir':request['boot_guard_dir'],
        'journal':request['journal'], 'port':request['port'], 'recovery_file':request['recovery_file'], 'volume':candidates[0]['Name'], 'image':image, 'boot_id':boot_id(),
    })


def recover_offline(request: dict, recovery) -> str:
    directory = Path(request['private_dir'])
    record = read_private_json(directory/'boot-target.json')
    expected = {'container':request['container_id'], 'pid':request['pid'], 'unit':request['unit'],
                'service':request['boot_guard_service'], 'guard_dir':request['boot_guard_dir'],
                'journal':request['journal'], 'port':request['port'], 'recovery_file':request['recovery_file']}
    if any(record.get(k) != v for k, v in expected.items()):
        raise ValueError('fixture recovery target mismatch')
    if record.get('boot_id') == boot_id() or not record.get('boot_id'):
        raise ValueError('host restart required')
    guard = FixtureBootGuard(Path(request['boot_guard_dir']), request['boot_guard_service'], recovery.run)
    if os.path.lexists(guard.marker):
        raise ValueError('fixture startup guard must be closed')
    guard.verify('inactive')
    journal = Path(request['journal'])
    if os.path.lexists(journal.with_name(journal.name+'.complete')):
        raise ValueError('completed fixture requires separate verification')
    journal_record = read_private_json(journal)
    batch = journal_record.get('batch', '')
    if journal_record.get('schema') != 1 or str(uuid.UUID(batch)) != batch:
        raise ValueError('invalid fixture recovery journal')
    if os.path.lexists(directory/'offline-outcome.json'):
        raise ValueError('fixture offline outcome already recorded')
    volume, image = record.get('volume', ''), record.get('image', '')
    if not re.fullmatch(r'logbook-fixture-[a-z0-9-]+', volume) or not re.fullmatch(r'sha256:[a-f0-9]{64}', image):
        raise ValueError('invalid recorded fixture storage')
    with (directory/'offline-recovery.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if recovery.run(['docker', 'ps', '-q', '--filter', 'volume='+volume]).strip():
            raise ValueError('fixture volume is in use')
        name = 'logbook-fixture-recovery-'+uuid.uuid4().hex
        try:
            recovery.run(['docker', 'run', '-d', '--rm', '--name', name, '--network=none',
                          '--cpus=1', '--memory=1g', '--memory-swap=1g',
                          '--mount', 'type=volume,src='+volume+',dst=/var/lib/memgraph',
                          image, '--bolt-port='+str(request['port'])])
            details = recovery.run(['docker', 'inspect', '--format',
                                    '{{.State.Pid}} {{.State.Running}} {{.HostConfig.NetworkMode}}', name]).split()
            if len(details) != 3 or not details[0].isdigit() or details[1:] != ['true', 'none']:
                raise ValueError('isolated fixture container required')
            mounts = json.loads(recovery.run(['docker', 'inspect', '--format', '{{json .Mounts}}', name]))
            matches = [m for m in mounts if m.get('Destination') == '/var/lib/memgraph']
            if (len(matches) != 1 or matches[0].get('Type') != 'volume'
                    or matches[0].get('Name') != volume or not matches[0].get('RW')):
                raise ValueError('isolated fixture volume mismatch')
            bundle = Path(request['recovery_file']).parent.parent
            result = recovery.run(['nsenter', '-t', details[0], '-n', '/usr/bin/env', 'PYTHONPATH='+str(bundle),
                                   '/usr/bin/python3', '-m', 'logbook.fixture_offline_worker',
                                   request['journal'], str(directory), str(request['port'])], timeout=120).strip()
            if result not in {'rolled-back', 'not-retained'}:
                raise ValueError('fixture rollback outcome unconfirmed')
        finally:
            recovery.run(['docker', 'stop', '--time', '30', name], timeout=45)
        if recovery.run(['docker', 'ps', '-q', '--filter', 'volume='+volume]).strip():
            raise ValueError('fixture volume remains in use')
        _save(directory/'offline-outcome.json', {**expected, 'volume':volume, 'boot_id':boot_id(), 'outcome':result})
        return result
