"""Run Logbook backup commands with the restricted Odin transfer settings."""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path


SETTINGS = {
    'LOGBOOK_BACKUP_ROOT': '192.168.1.3:/',
    'LOGBOOK_BACKUP_SSH_IDENTITY_FILE': str(Path.home()/'.ssh/odin-logbook-backup'),
    'LOGBOOK_BACKUP_RESTRICTED_RSYNC': 'true',
    'LOGBOOK_BACKUP_RSYNC_EXECUTABLE': '/opt/homebrew/bin/rsync',
}


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('backup-run', 'backup-restore-drill'))
    parser.add_argument('--env', required=True)
    parser.add_argument('--repo-root')
    parser.add_argument('--backup')
    parser.add_argument('--execute', action='store_true')
    options = parser.parse_args(argv)
    if options.action == 'backup-run' and (not options.repo_root or options.backup):
        parser.error('backup-run requires --repo-root and does not accept --backup')
    if options.action == 'backup-restore-drill' and (not options.backup or options.repo_root or options.execute):
        parser.error('backup-restore-drill requires --backup')
    source = Path(options.env).read_text(encoding='utf-8')
    lines = [line for line in source.splitlines() if line.split('=', 1)[0] not in SETTINGS]
    lines.extend(f'{key}={value}' for key, value in SETTINGS.items())
    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp)/'backup.env'
        env.write_text('\n'.join(lines)+'\n', encoding='utf-8')
        env.chmod(0o600)
        command = [sys.executable, '-m', 'logbook.cli', options.action, '--env', str(env)]
        if options.action == 'backup-run':
            command += ['--repo-root', options.repo_root]
            if options.execute:
                command.append('--execute')
        else:
            command += ['--backup', options.backup]
        return subprocess.run(command, check=False).returncode


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(run(sys.argv[1:]))
