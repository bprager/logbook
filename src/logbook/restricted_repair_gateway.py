#!/usr/bin/python3 -I
"""Constrain SSH requests to fixed named repair commands; no shell is invoked."""
from __future__ import annotations

import os
import re
import shlex
import sys

INSTALLER = '/usr/local/libexec/logbook-repair-bundle-installer'
DISPATCHER = '/usr/local/libexec/logbook-repair-dispatcher'
RECEIVER = '/usr/local/libexec/logbook-repair-release-receiver'
ACTIONS = frozenset({'named-discover', 'named-plan-discover', 'named-check', 'named-stage', 'named-preflight', 'named-launch', 'named-cancel', 'named-status',
                     'named-outcome', 'named-offline', 'named-authorize'})
RECOVERY_ACTIONS = frozenset({'named-status', 'named-outcome', 'named-cancel'})


def permitted_command(command: str, *, recovery_only: bool = False) -> list[str]:
    if not isinstance(command, str) or len(command) > 1024:
        raise ValueError('invalid repair command')
    try:
        args = shlex.split(command)
    except ValueError as error:
        raise ValueError('invalid repair command') from error
    if not recovery_only and args == ['sudo', '-n', RECEIVER]:
        return ['/usr/bin/sudo', '-n', RECEIVER]
    actions = RECOVERY_ACTIONS if recovery_only else ACTIONS
    if len(args) == 4 and args[:3] == ['sudo', '-n', DISPATCHER] and args[3] in actions:
        return ['/usr/bin/sudo', '-n', DISPATCHER, args[3]]
    if not recovery_only and len(args) == 5 and args[:3] == ['sudo', '-n', INSTALLER]:
        match = re.fullmatch(r'/var/lib/logbook-graph-guard/bundles/bundle-([a-f0-9]{32})', args[3])
        if match and args[4] == '/var/lib/logbook-graph-guard/bundles/window-' + match.group(1):
            return ['/usr/bin/sudo', '-n', INSTALLER, args[3], args[4]]
    raise ValueError('repair command rejected')


def main() -> None:  # pragma: no cover
    if sys.argv[1:] not in ([], ['--recovery']):
        raise SystemExit(64)
    command = permitted_command(os.environ.get('SSH_ORIGINAL_COMMAND', ''),
                                recovery_only=sys.argv[1:] == ['--recovery'])
    os.execv(command[0], command)


if __name__ == '__main__':  # pragma: no cover
    main()
