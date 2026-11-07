"""Experimental persistent startup guard for a fixture-only service."""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Callable


def verify_fixture_binding(recovery, service: str) -> None:
    if not re.fullmatch(r'logbook-fixture-[a-z0-9-]+\.service', service):
        raise ValueError('fixture binding requires fixture service')
    name = service.removesuffix('.service')
    pid = recovery.run(['systemctl', 'show', service, '-p', 'MainPID', '--value']).strip()
    if not pid.isdigit() or int(pid) < 1:
        raise ValueError('fixture service binding invalid')
    executable = recovery.run(['readlink', '-f', '/proc/'+pid+'/exe']).strip()
    arguments = recovery.run(['cat', '/proc/'+pid+'/cmdline']).split('\0')
    if (executable != '/usr/bin/docker' or arguments[:4] != ['/usr/bin/docker', 'run', '--name', name]
            or arguments.count('--name') != 1 or any(arg.startswith('--name=') for arg in arguments)):
        raise ValueError('fixture service binding invalid')
    target = recovery.run(['docker', 'inspect', '--format',
                           '{{.Id}} {{.Name}} {{.State.Pid}} {{.State.Running}}',
                           recovery.container_id]).strip()
    if target != f'{recovery.container_id} /{name} {recovery.pid} true':
        raise ValueError('fixture container binding invalid')
    if recovery.run(['systemctl', 'show', service, '-p', 'MainPID', '--value']).strip() != pid:
        raise ValueError('fixture service binding changed')


class FixtureBootGuard:
    def __init__(self, directory: Path, service: str, run: Callable[[list[str]], str]):
        if not re.fullmatch(r'logbook-fixture-[a-z0-9-]+\.service', service):
            raise ValueError('fixture service required')
        self.directory, self.service, self.run = directory, service, run
        self.marker = directory/'start-allowed'

    def verify(self, expected_state: str = 'active') -> None:
        info = self.directory.lstat()
        if (not self.directory.is_absolute() or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise ValueError('private fixture directory required')
        status = self.run(['systemctl', 'show', self.service, '-p', 'LoadState', '-p', 'ActiveState', '--value'])
        if status.splitlines() != ['loaded', expected_state]:
            raise ValueError('active fixture service required')
        name = ''.join(c if c.isascii() and c.isalnum() else f'_{ord(c):02x}' for c in self.service)
        result = self.run(['busctl', '--json=short', 'get-property', 'org.freedesktop.systemd1',
                           '/org/freedesktop/systemd1/unit/'+name, 'org.freedesktop.systemd1.Unit', 'Conditions'])
        conditions = json.loads(result)
        expected = ['ConditionPathExists', False, False, str(self.marker)]
        if conditions.get('type') != 'a(sbbsi)' or not any(row[:4] == expected for row in conditions.get('data', [])):
            raise ValueError('fixture startup condition missing')

    def _check_marker(self) -> None:
        fd = os.open(self.marker, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'r', encoding='utf-8') as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077 or source.read(32) != 'allowed\n'):
                raise ValueError('invalid fixture start marker')

    def _sync(self) -> None:
        fd = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def disarm(self) -> None:
        self.verify()
        self._check_marker()
        self.marker.unlink()
        self._sync()

    def allow(self, expected_state: str = 'active') -> None:
        self.verify(expected_state)
        try:
            fd = os.open(self.marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            self._check_marker()
            return
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            output.write('allowed\n')
            output.flush()
            os.fsync(output.fileno())
        self._sync()
