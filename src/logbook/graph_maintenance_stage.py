"""Stage a plan into an already provisioned private maintenance directory."""
from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

from logbook.graph_maintenance_worker import private_directory


def stage_plan(request: dict, bundle: Path) -> None:
    private = Path(request['private_dir'])
    journal = Path(request['journal'])
    private_directory(private)
    private_directory(journal.parent)
    private_directory(bundle)
    if (Path(request['worker']) != private/'worker' or
            Path(request['recovery_worker']) != private/'restore' or
            Path(request['recovery_file']) != bundle/'logbook/graph_maintenance_recovery.py'):
        raise ValueError('staging paths do not match trusted locations')
    plan = request['plan_data']
    encoded = json.dumps(plan, sort_keys=True).encode()
    if not isinstance(plan, dict) or len(encoded) > 5000000:
        raise ValueError('bounded plan object required')
    worker_request = {key: request[key] for key in ('container_id', 'pid', 'port', 'unit', 'journal')}
    worker_request['plan'] = str(private/'plan.json')
    def put(name: str, data: bytes, mode: int) -> None:
        fd = os.open(private/name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    put('plan.json', encoded, 0o600)
    if request.get('profile') == 'named-graph':
        put('plan-proof.json', json.dumps(request['plan_proof'], sort_keys=True).encode(), 0o600)
    put('request.json', json.dumps(worker_request, sort_keys=True).encode(), 0o600)
    for action, name in (('apply','worker'), ('recover','restore')):
        command = ['/usr/bin/nsenter', '-t', str(request['pid']), '-n', '/usr/bin/env',
                   f'PYTHONPATH={bundle}', '/usr/bin/python3', '-m',
                   'logbook.graph_maintenance_worker', action, str(private/'request.json')]
        put(name, ('#!/bin/sh\nexec '+shlex.join(command)+'\n').encode(), 0o700)
    fd = os.open(private, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
