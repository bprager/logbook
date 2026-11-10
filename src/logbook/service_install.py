"""Generate the boot-time service package; installation remains an explicit step."""
from __future__ import annotations

import argparse
import getpass
import json
import plistlib
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from logbook.config import load_app_config
from logbook.remote_config import ServerConfig, load_remote_config


def write_package(env_path, server_path, repo, output, odin_host):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,252}', odin_host):
        raise ValueError('Use a plain SSH host alias for Odin')
    config = load_app_config(env_path)
    server = load_remote_config(server_path, ServerConfig)
    if server.ledger_path != config.sqlite_path:
        raise ValueError('Receiver and queue processor must use the same production ledger')
    if not config.api:
        raise ValueError('Configure the status API before installing supervision')
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    python = str(Path(sys.executable).absolute())
    api_host = '127.0.0.1' if config.api.bind_host in ('0.0.0.0', '127.0.0.1') else config.api.bind_host
    services = [
        {'name': 'receiver', 'command': [python, '-m', 'logbook.remote_cli', 'serve',
                                        '--config', str(server_path.resolve())],
         'health_url': f'http://127.0.0.1:{server.port}/health'},
        {'name': 'api', 'command': [python, '-m', 'logbook.cli', 'serve-api',
                                   '--env', str(env_path.resolve())],
         'health_url': f'http://{api_host}:{config.api.port}/health'},
        {'name': 'queue', 'command': [python, '-m', 'logbook.cli', 'process-queued',
                                     '--env', str(env_path.resolve())], 'interval': 60, 'timeout': 1800},
        {'name': 'odin', 'health_url': config.odin.api_base_url.rstrip('/') + '/health',
         'health_field': 'healthy', 'health_value': True,
         'restart_command': ['/usr/bin/ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                             '-o', 'StrictHostKeyChecking=yes', '-o', 'ForwardX11=no',
                             '-o', 'Hostname=' + str(urlsplit(config.odin.api_base_url).hostname), odin_host,
                             'systemctl --user restart logbook-odin-worker.service']},
    ]
    settings = output / 'supervisor.json'
    settings.write_text(json.dumps({'root': str(config.processing_root / 'services'),
                                   'ledger_path': str(config.sqlite_path), 'services': services}, indent=2))
    settings.chmod(0o600)
    logs = config.processing_root / 'services' / 'logs'
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = output / 'local.logbook.supervisor.plist'
    path.write_bytes(plistlib.dumps({
        'Label': 'local.logbook.supervisor', 'UserName': getpass.getuser(),
        'ProgramArguments': [python, '-m', 'logbook.supervisor', '--config', str(settings)],
        'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 60, 'ExitTimeOut': 60,
        'WorkingDirectory': str(repo.resolve()), 'ProcessType': 'Background',
        'EnvironmentVariables': {'PYTHONUNBUFFERED': '1', 'PYTHONPATH': str(repo.resolve() / 'src'), 'HOME': str(Path.home()),
                                 'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'},
        'StandardOutPath': str(logs / 'supervisor.out.log'),
        'StandardErrorPath': str(logs / 'supervisor.err.log'),
    }))
    return settings, path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', type=Path, default=Path('.env'))
    parser.add_argument('--server-config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--odin-host', default='odin')
    args = parser.parse_args(argv)
    for path in write_package(args.env, args.server_config, Path.cwd(), args.output.resolve(), args.odin_host):
        print(path)


if __name__ == '__main__':  # pragma: no cover - package CLI exercised during deployment
    main()
