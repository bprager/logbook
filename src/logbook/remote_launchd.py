"""Generate signed remote packages; never install or load services implicitly."""
from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from logbook.launchd import _render_mount_runner_app, _write_app_bundle
from logbook.remote_config import AgentConfig


def write_agent_package(config: AgentConfig, config_path: Path, output: Path,
                        repo_root: Path, signing_identity: str):
    if not signing_identity or signing_identity == '-':
        raise ValueError('use a persistent signing certificate for removable-volume permissions')
    output = output.resolve()
    repo_root = repo_root.resolve()
    logs = config.root / 'logs'
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    app = _render_mount_runner_app(
        bundle_path=output / 'LogbookRemoteAgent.app', python_executable=sys.executable,
        repo_root=repo_root, src_path=repo_root / 'src', env_path=config_path.resolve(),
        recorder_dir=config.mount_path / config.recordings_path,
        module='logbook.remote_cli', command='agent-run', config_arg='--config',
        bundle_identifier='ws.prager.logbook.remote-agent', display_name='Logbook Remote Agent')
    _write_app_bundle(app)
    subprocess.run(['/usr/bin/codesign', '--force', '--sign', signing_identity,
                    '--identifier', 'ws.prager.logbook.remote-agent', str(app.bundle_path)], check=True)
    subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(app.bundle_path)], check=True)
    path = output / 'local.logbook.remote-agent.plist'
    path.write_bytes(plistlib.dumps({
        'Label': 'local.logbook.remote-agent',
        'ProgramArguments': ['/usr/bin/open', '-W', '-n', str(app.bundle_path)],
        'RunAtLoad': True, 'StartOnMount': True, 'StartInterval': 60,
        'ProcessType': 'Background', 'WorkingDirectory': str(repo_root),
        'StandardOutPath': str(logs / 'agent.out.log'),
        'StandardErrorPath': str(logs / 'agent.err.log'),
    }))
    paths = (path, app.bundle_path)
    if config.ssh_host:
        paths += (write_tunnel_package(config, output),)
    return paths


def write_tunnel_package(config: AgentConfig, output: Path):
    config.validate()
    if not config.ssh_host:
        raise ValueError('configure ssh_host before packaging an SSH tunnel')
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    logs = config.root / 'logs'
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    options = ('BatchMode=yes', 'StrictHostKeyChecking=yes', 'ExitOnForwardFailure=yes',
                   'ConnectTimeout=15', 'ServerAliveInterval=15', 'ServerAliveCountMax=3',
                   'ForwardAgent=no', 'ForwardX11=no', 'ControlMaster=no', 'ControlPath=none',
                   'ControlPersist=no', 'ForkAfterAuthentication=no')
    # OpenSSH propagates -F to ProxyJump children, unlike command-line -o flags.
    # First values win, so these precede the user's existing aliases and keys.
    ssh_config = output / 'remote-ssh.conf'
    ssh_config.write_text('Host *\n' + ''.join('    ' + option.replace('=', ' ', 1) + '\n'
                                             for option in options) +
                          'Include ~/.ssh/config\n')
    ssh_config.chmod(0o600)
    args = ['/usr/bin/ssh', '-N', '-T', '-F', str(ssh_config)]
    for option in (*options, 'ClearAllForwardings=no'):
        args.extend(['-o', option])
    args.extend(['-L', f'127.0.0.1:{urlsplit(config.server_url).port}:127.0.0.1:{config.ssh_remote_port}',
                 config.ssh_host])
    path = output / 'local.logbook.remote-tunnel.plist'
    path.write_bytes(plistlib.dumps({
        'Label': 'local.logbook.remote-tunnel', 'ProgramArguments': args,
        'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 30,
        'ProcessType': 'Background',
        'StandardOutPath': str(logs / 'tunnel.out.log'),
        'StandardErrorPath': str(logs / 'tunnel.err.log'),
    }))
    return path


def write_server_package(config, config_path: Path, env_path: Path, output: Path, repo_root: Path):
    output.mkdir(parents=True, exist_ok=True)
    logs = config.root / 'logs'
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    common = {'RunAtLoad': True, 'WorkingDirectory': str(repo_root.resolve()),
              'EnvironmentVariables': {'PYTHONPATH': str(repo_root.resolve() / 'src')},
              'ProcessType': 'Background'}
    paths = []
    for name, args, schedule in (
        ('remote-ingest', ['logbook.remote_cli', 'serve', '--config', str(config_path.resolve())],
         {'KeepAlive': True, 'ThrottleInterval': 10}),
        ('queued-worker', ['logbook.cli', 'process-queued', '--env', str(env_path.resolve())],
         {'StartInterval': 60}),
    ):
        label = 'local.logbook.' + name
        path = output / (label + '.plist')
        path.write_bytes(plistlib.dumps({
            **common, **schedule, 'Label': label,
            'ProgramArguments': [sys.executable, '-m', *args],
            'StandardOutPath': str(logs / (name + '.out.log')),
            'StandardErrorPath': str(logs / (name + '.err.log')),
        }))
        paths.append(path)
    return tuple(paths)
