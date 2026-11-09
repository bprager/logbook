"""Source-account holder for a restricted named repair operator."""
from __future__ import annotations

import json
import os
import pwd
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from logbook.graph_maintenance_launcher import with_logbook_lock_descriptors
from logbook.named_repair_dispatcher import validate_request_shape
from logbook.processing_restart_guard import arm_guard, clear_guard
from logbook.source_plan_broker import trusted_config

ROOT = Path('/Users/bernd/VoiceIngest')
IDENTITY = '/Users/bernd/.ssh/odin-logbook-repair-operator'
KNOWN_HOSTS = '/Library/Application Support/LogbookRepairOperator/known_hosts'


class FixedRecovery:
    """Only query the pinned Odin dispatcher; no caller-controlled connection."""

    def __init__(self, request: dict):
        validate_request_shape('named-status', request)
        self.request = request

    def _invoke(self, action: str) -> str:
        if action not in {'named-status', 'named-outcome'}:
            raise ValueError('unsupported recovery query')
        command = shlex.join(['sudo', '-n', '/usr/local/libexec/logbook-repair-dispatcher', action])
        result = subprocess.run(
            ['/usr/bin/ssh', '-F', '/dev/null', '-i', IDENTITY, '-o', 'IdentitiesOnly=yes',
             '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', '-o', 'StrictHostKeyChecking=yes',
             '-o', 'HostKeyAlias=odin', '-o', f'UserKnownHostsFile="{KNOWN_HOSTS}"',
             'logbook-repair@odin.local', command], input=json.dumps(self.request),
            text=True, capture_output=True, timeout=15,
            env={'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8', 'HOME':'/Users/bernd'})
        if result.returncode:
            raise ConnectionError('recovery query failed')
        return result.stdout.strip()

    def recovered(self) -> bool:
        return self._invoke('named-status') == 'RECOVERED'

    def outcome(self) -> str:
        result = self._invoke('named-outcome')
        if result not in {'retained', 'rolled-back', 'not-retained'}:
            raise RuntimeError('recovery outcome unconfirmed')
        return result


def hold_window(config, remote_factory, source, output) -> None:
    def operation(_pipeline: int, _graph: int) -> None:
        output.write('LOCKED\n')
        output.flush()
        raw = source.readline(8193)
        if len(raw) > 8192 or not raw.endswith('\n'):
            raise ValueError('invalid bounded repair request')
        remote = remote_factory(json.loads(raw))
        unit = remote.request.get('unit', '')
        if not isinstance(unit, str) or not re.fullmatch(r'logbook-graph-maintenance-[a-f0-9]{32}\.service', unit):
            raise ValueError('invalid maintenance unit')
        if remote.recovered():
            raise ValueError('maintenance unit already recovered')
        arm_guard(config.processing_root, unit)
        try:
            output.write('ARMED\n')
            output.flush()
        except BrokenPipeError:
            pass
        while True:
            try:
                if remote.recovered():
                    remote.outcome()
                    clear_guard(config.processing_root, unit)
                    return
            except Exception:
                pass
            time.sleep(0.5)

    with_logbook_lock_descriptors(config, operation)


def main() -> None:  # pragma: no cover
    if os.geteuid() != pwd.getpwnam('bernd').pw_uid or len(sys.argv) != 1:
        raise ValueError('fixed source account and no arguments required')
    trusted_config(Path(KNOWN_HOSTS))
    hold_window(SimpleNamespace(processing_root=ROOT), FixedRecovery, sys.stdin, sys.stdout)
