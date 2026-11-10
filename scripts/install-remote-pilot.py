"""Install the already-enrolled Mac pilot; never touch production settings."""
import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path


def main(argv=None):
    from logbook.remote_config import AgentConfig, load_remote_config
    from logbook.remote_launchd import write_agent_package

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--signing-identity', required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    home = Path.home()
    pilot = home / 'Library/Application Support/LogbookRemotePilot'
    settings = pilot / 'remote-agent.json'
    package = home / 'Applications/LogbookRemote/pilot-package'
    agents = home / 'Library/LaunchAgents'
    names = ('local.logbook.remote-tunnel.plist', 'local.logbook.remote-agent.plist')
    if any((agents / name).exists() or (agents / name).is_symlink() for name in names):
        raise SystemExit('An installed service already exists. Stop and send this message.')
    config = json.loads(settings.read_text())
    if Path(config['root']).resolve() != pilot.resolve():
        raise SystemExit('Expected the separate pilot buffer; nothing installed.')
    config.update(ssh_host='jump-mimir', ssh_remote_port=18791,
                  server_url='http://127.0.0.1:18791', vpn_transport=False)
    settings.write_text(json.dumps(config, indent=2))
    write_agent_package(load_remote_config(settings, AgentConfig), settings, package,
                        Path(__file__).resolve().parents[1], args.signing_identity)
    agents.mkdir(parents=True, exist_ok=True)
    for name in names:
        destination = agents / name
        shutil.copy2(package / name, destination)
        subprocess.run(['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(destination)], check=True)
        print('Started:', name)


if __name__ == '__main__':
    main()
