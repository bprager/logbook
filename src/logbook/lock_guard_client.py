"""Restricted operator client for the source-account recovery holder."""
from __future__ import annotations

import json
import select
import subprocess
from pathlib import Path
from typing import Callable

from logbook.graph_maintenance_cross_host import NamedGraphMaintenance

COMMAND = ['/usr/bin/sudo', '-n', '-u', 'bernd', '/usr/local/libexec/logbook-lock-guardian']
OPERATOR_KEY = Path('/Library/Application Support/LogbookRepairOperator/private/odin-repair')


def coordinate_named_handoff(remote: NamedGraphMaintenance, refresh: Callable[[], dict], *,
                             timeout_seconds: float) -> None:
    if remote.host != 'logbook-repair@odin' or remote.identity != OPERATOR_KEY:
        raise ValueError('fixed restricted repair connection required')
    child = subprocess.Popen(COMMAND, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True, start_new_session=True,
                             env={'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8'})
    assert child.stdin is not None and child.stdout is not None

    def expect(message: str) -> None:
        ready, _, _ = select.select([child.stdout], [], [], 15)
        if not ready or child.stdout.readline().strip() != message:
            raise RuntimeError('source lock holder unavailable')

    try:
        expect('LOCKED')
        plan = refresh()
        encoded = json.dumps(remote.request)
        if len(encoded.encode()) > 8191 or '\n' in encoded:
            raise ValueError('repair request exceeds limit')
        child.stdin.write(encoded + '\n')
        child.stdin.flush()
        expect('ARMED')
        remote.stage(plan)
        remote.preflight()
        failure = None
        try:
            remote.launch()
        except Exception as error:
            failure = error
        try:
            result = child.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as error:
            raise TimeoutError(f'recovery unconfirmed; independent holder pid {child.pid}') from error
        if result != 0:
            raise RuntimeError('source lock holder stopped before recovery')
        if failure is not None:
            raise failure
    finally:
        child.stdin.close()
