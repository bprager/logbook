"""Separate entry point; Mac agents never load production processing settings."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from logbook.remote_config import AgentConfig, ServerConfig, load_remote_config


def main(argv=None):
    parser = argparse.ArgumentParser(prog='logbook-remote')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('agent-run', 'serve', 'package-agent'):
        command = commands.add_parser(name)
        command.add_argument('--config', required=True, type=Path)
        if name == 'package-agent':
            command.add_argument('--output', required=True, type=Path)
            command.add_argument('--repo', required=True, type=Path)
            command.add_argument('--signing-identity', required=True)
    args = parser.parse_args(argv)
    config = load_remote_config(args.config, ServerConfig if args.command == 'serve' else AgentConfig)
    if args.command == 'serve':
        import uvicorn
        from logbook.remote_api import create_ingest_app
        uvicorn.run(create_ingest_app(config), host=config.bind_host, port=config.port,
                    workers=1, proxy_headers=False, access_log=False, limit_concurrency=16,
                    timeout_keep_alive=5)
    elif args.command == 'agent-run':
        from logbook.remote_agent import run_agent
        print(json.dumps(run_agent(config), sort_keys=True))
    else:
        from logbook.remote_launchd import write_agent_package
        for path in write_agent_package(config, args.config, args.output, args.repo,
                                       args.signing_identity):
            print(path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
