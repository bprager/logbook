"""Fixture-only recovery after the protected graph host has restarted."""
from __future__ import annotations

from contextlib import nullcontext
import fcntl
import json
import os
import re
import subprocess
import uuid
from pathlib import Path

from logbook.recovery_startup_evidence import StartupEvidence
from logbook.fixture_boot_guard import FixtureBootGuard, MemgraphBootGuard, verify_memgraph_binding
from logbook.graph_maintenance_worker import completion_matches, private_directory, read_private_json


NAMED_GUARD_DIR = Path('/var/lib/logbook-graph-guard')
NAMED_VOLUME = 'memgraph-data-v313-20261011'


def offline_memory_limit(named: bool) -> str:
    """Keep named recovery bounded and leave at least the same RAM available."""
    if not named:
        return '1g'
    match = re.search(r'^MemAvailable:\s+([0-9]+) kB$',
                      Path('/proc/meminfo').read_text(), re.MULTILINE)
    if match is None or int(match[1]) < 36 * 1024 * 1024:
        raise ValueError('36GiB available memory headroom required for named recovery')
    return '18g'


def stop_offline_container(recovery, name: str) -> None:
    try:
        recovery.run(['docker', 'stop', '--time', '30', name], timeout=45)
    except subprocess.CalledProcessError:
        # --rm may already have removed a killed container. Confirm absence;
        # daemon errors and a container that still exists remain failures.
        if recovery.run(['docker', 'ps', '-aq', '--filter', 'name=^/'+name+'$']).strip():
            raise


def _validate_named(request: dict) -> None:
    if (request.get('boot_guard_service') != 'memgraph.service'
            or Path(request.get('boot_guard_dir', '')) != NAMED_GUARD_DIR
            or request.get('port') != 7697):
        raise ValueError('named graph target mismatch')


def _volume_valid(volume: str, named: bool) -> bool:
    return volume == NAMED_VOLUME if named else bool(re.fullmatch(r'logbook-fixture-[a-z0-9-]+', volume))


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


def _discard_stale_named_receipt(directory: Path, expected: dict, volume: str,
                                 original_boot: str, batch: str | None, completed: bool) -> None:
    path = directory/'offline-outcome.json'
    if not os.path.lexists(path):
        return
    receipt = read_private_json(path)
    receipt_boot = receipt.get('boot_id')
    outcome = receipt.get('outcome')
    if (not isinstance(receipt_boot, str) or not receipt_boot
            or receipt_boot in {original_boot, boot_id()}):
        raise ValueError('fixture offline outcome already recorded')
    valid = {**expected, 'volume':volume, 'boot_id':receipt_boot, 'outcome':outcome, 'batch':batch}
    if batch is None:
        valid['phase'] = 'before-journal'
        valid_outcome = outcome == 'not-retained'
    else:
        valid_outcome = outcome in ({'retained'} if completed else {'rolled-back', 'not-retained'})
    if not valid_outcome or receipt != valid:
        raise ValueError('invalid stale offline recovery receipt')
    path.unlink()
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def record_fixture_storage(request: dict, recovery, *, _named: bool = False) -> None:
    if _named:
        _validate_named(request)
        verify_memgraph_binding(recovery)
    directory = Path(request['private_dir'])
    private_directory(directory)
    mounts = json.loads(recovery.run(['docker', 'inspect', '--format', '{{json .Mounts}}', recovery.container_id]))
    candidates = [m for m in mounts if m.get('Destination') == '/var/lib/memgraph']
    if (len(candidates) != 1 or candidates[0].get('Type') != 'volume' or not candidates[0].get('RW')
            or not _volume_valid(candidates[0].get('Name', ''), _named)):
        raise ValueError('dedicated fixture volume required')
    image = recovery.run(['docker', 'inspect', '--format', '{{.Image}}', recovery.container_id]).strip()
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', image):
        raise ValueError('fixture image ID required')
    _save(directory/'boot-target.json', {
        'container':recovery.container_id, 'pid':recovery.pid, 'unit':request['unit'],
        'service':request['boot_guard_service'], 'guard_dir':request['boot_guard_dir'],
        'journal':request['journal'], 'port':request['port'], 'recovery_file':request['recovery_file'], 'volume':candidates[0]['Name'], 'image':image, 'boot_id':boot_id(),
    })


def named_offline_required(request: dict, recovery) -> bool:
    """Select only a proven restarted, matching, guarded and inactive target."""
    _validate_named(request)
    record = read_private_json(Path(request['private_dir'])/'boot-target.json')
    expected = {'container':request['container_id'],'pid':request['pid'],'unit':request['unit'],
                'service':request['boot_guard_service'],'guard_dir':request['boot_guard_dir'],
                'journal':request['journal'],'port':request['port'],'recovery_file':request['recovery_file']}
    if (not record.get('boot_id') or record['boot_id'] == boot_id()
            or any(record.get(k) != v for k,v in expected.items())):
        return False
    guard = MemgraphBootGuard(Path(request['boot_guard_dir']),recovery.run)
    if os.path.lexists(guard.marker):
        # An interrupted authorizer may have created the valid marker before
        # starting the service. Only a verified current receipt permits replay.
        from logbook.fixture_recovery_release import verify_named_graph_receipt
        try:
            guard._check_marker()
            verify_named_graph_receipt(request)
        except (OSError,ValueError):
            return False
    guard.verify('inactive')
    return True


def recover_offline(request: dict, recovery, *, _named: bool = False) -> str:
    if _named:
        _validate_named(request)
    directory = Path(request['private_dir'])
    record = read_private_json(directory/'boot-target.json')
    expected = {'container':request['container_id'], 'pid':request['pid'], 'unit':request['unit'],
                'service':request['boot_guard_service'], 'guard_dir':request['boot_guard_dir'],
                'journal':request['journal'], 'port':request['port'], 'recovery_file':request['recovery_file']}
    if any(record.get(k) != v for k, v in expected.items()):
        raise ValueError('fixture recovery target mismatch')
    if record.get('boot_id') == boot_id() or not record.get('boot_id'):
        raise ValueError('host restart required')
    guard = (MemgraphBootGuard(Path(request['boot_guard_dir']), recovery.run) if _named
             else FixtureBootGuard(Path(request['boot_guard_dir']), request['boot_guard_service'], recovery.run))
    if os.path.lexists(guard.marker):
        raise ValueError('fixture startup guard must be closed')
    guard.verify('inactive')
    journal = Path(request['journal'])
    private_directory(journal.parent)
    completed = os.path.lexists(journal.with_name(journal.name+'.complete'))
    if _named and not os.path.lexists(journal):
        # The worker fsyncs the journal and its directory before any graph write.
        # A missing journal after reboot therefore denotes a pre-write interruption,
        # assuming the root-controlled journal storage has not been altered.
        if completed or os.path.lexists(journal.with_name(journal.name+'.outcome')):
            raise ValueError('conflicting pre-journal evidence')
        volume, image = record.get('volume', ''), record.get('image', '')
        if not _volume_valid(volume, True) or not re.fullmatch(r'sha256:[a-f0-9]{64}', image):
            raise ValueError('invalid recorded fixture storage')
        with (directory/'offline-recovery.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if recovery.run(['docker', 'ps', '-q', '--filter', 'volume='+volume]).strip():
                raise ValueError('fixture volume is in use')
            _discard_stale_named_receipt(directory, expected, volume, record['boot_id'], None, False)
            if os.path.lexists(journal) or os.path.lexists(journal.with_name(journal.name+'.complete')):
                raise ValueError('journal appeared during recovery')
            _save(directory/'offline-outcome.json', {**expected, 'volume':volume, 'boot_id':boot_id(),
                                                      'outcome':'not-retained', 'batch':None,
                                                      'phase':'before-journal'})
        return 'not-retained'
    journal_record = read_private_json(journal)
    batch = journal_record.get('batch', '')
    if journal_record.get('schema') != 1 or str(uuid.UUID(batch)) != batch:
        raise ValueError('invalid fixture recovery journal')
    if completed and not completion_matches(journal, request, batch):
        raise ValueError('fixture completion does not match target')
    mode = 'retain' if completed else 'rollback'
    if not _named and os.path.lexists(directory/'offline-outcome.json'):
        raise ValueError('fixture offline outcome already recorded')
    volume, image = record.get('volume', ''), record.get('image', '')
    if not _volume_valid(volume, _named) or not re.fullmatch(r'sha256:[a-f0-9]{64}', image):
        raise ValueError('invalid recorded fixture storage')
    with (directory/'offline-recovery.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if recovery.run(['docker', 'ps', '-q', '--filter', 'volume='+volume]).strip():
            raise ValueError('fixture volume is in use')
        if _named:
            _discard_stale_named_receipt(directory, expected, volume, record['boot_id'], batch, completed)
        memory = offline_memory_limit(_named)
        name = 'logbook-fixture-recovery-'+uuid.uuid4().hex
        try:
            recovery.run(['docker', 'run', '-d', '--rm', '--name', name, '--network=none',
                          '--cpus='+('2' if _named else '1'), '--memory='+memory, '--memory-swap='+memory,
                          '--mount', 'type=volume,src='+volume+',dst=/var/lib/memgraph',
                          image, '--bolt-port='+str(request['port']),
                          *(['--storage-recovery-thread-count=1', '--log-level=INFO',
                             '--storage-wal-enabled=true', '--storage-wal-file-flush-every-n-tx=1',
                             '--storage-snapshot-interval-sec=300', '--storage-snapshot-on-exit=false']
                            if _named else [])])
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
            with StartupEvidence(details[0],directory) if _named else nullcontext():
                result = recovery.run(['nsenter', '-t', details[0], '-n', '/usr/bin/env', 'PYTHONPATH='+str(bundle),
                                       '/usr/bin/python3', '-m', 'logbook.fixture_offline_worker',
                                       request['journal'], str(directory), str(request['port']), mode,
                                       *(['120'] if _named else [])], timeout=180 if _named else 120).strip()
            if result not in ({'retained'} if completed else {'rolled-back', 'not-retained'}):
                raise ValueError('fixture rollback outcome unconfirmed')
        finally:
            stop_offline_container(recovery, name)
        if recovery.run(['docker', 'ps', '-q', '--filter', 'volume='+volume]).strip():
            raise ValueError('fixture volume remains in use')
        _save(directory/'offline-outcome.json', {**expected, 'volume':volume, 'boot_id':boot_id(), 'outcome':result, 'batch':batch})
        return result


def record_named_graph_storage(request: dict, recovery) -> None:
    """Record the exact named graph target; not exposed by the operator."""
    record_fixture_storage(request, recovery, _named=True)


def recover_named_graph_offline(request: dict, recovery) -> str:
    """Recover that target offline; not exposed by the operator."""
    return recover_offline(request, recovery, _named=True)
