"""Generate signed remote packages; never install or load services implicitly."""
from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

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
        bundle_identifier='ws.prager.logbook.remote-agent')
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
    return (path, app.bundle_path)


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
