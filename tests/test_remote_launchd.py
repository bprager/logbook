import plistlib
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from logbook.remote_launchd import write_agent_package
from logbook.remote_config import AgentConfig


class RemoteLaunchdTests(TestCase):
    def test_package_is_mount_login_and_retry_driven_with_no_cleanup(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = AgentConfig(root / 'buffer', root / 'SONY', 'SONY', 'uuid',
                                 'REC_FILE/FOLDER01', 'UTC', 'https://mimir', root / 'token')
            original_run = subprocess.run
            def execute(command, **kwargs):
                if command[0] == 'cc':
                    return original_run(command, **kwargs)
                return subprocess.CompletedProcess(command, 0)
            with patch('logbook.remote_launchd.subprocess.run', side_effect=execute) as run:
                paths = write_agent_package(config, root / 'config.json', root / 'package',
                                            Path.cwd(), 'Logbook Local Signing')
            plist = plistlib.loads(paths[0].read_bytes())
            self.assertTrue(plist['StartOnMount'])
            self.assertTrue(plist['RunAtLoad'])
            self.assertEqual(plist['StartInterval'], 60)
            self.assertNotIn('cleanup', paths[0].read_text())
            app = root / 'package' / 'LogbookRemoteAgent.app'
            info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
            self.assertEqual(info['CFBundleIdentifier'], 'ws.prager.logbook.remote-agent')
            source = (app / 'Contents/MacOS/LogbookMountRunner.c').read_text()
            self.assertIn('logbook.remote_cli', source)
            self.assertIn('agent-run', source)
            self.assertNotIn('process-mounted-recorder', source)
            self.assertIn('codesign', str(run.call_args_list))
            with self.assertRaises(ValueError):
                write_agent_package(config, root / 'config.json', root / 'package', Path.cwd(), '-')

    def test_server_package_uses_existing_ledger_pipeline_without_cleanup(self):
        from logbook.remote_launchd import write_server_package
        from logbook.remote_config import ServerConfig
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = ServerConfig(root / 'voice.sqlite', root / 'remote', root / 'devices')
            paths = write_server_package(config, root / 'remote.json', root / '.env',
                                         root / 'output', Path.cwd())
            api, worker = (plistlib.loads(path.read_bytes()) for path in paths)
            self.assertTrue(api['KeepAlive'])
            self.assertIn('serve', api['ProgramArguments'])
            self.assertIn('process-queued', worker['ProgramArguments'])
            self.assertEqual(worker['StartInterval'], 60)
            self.assertNotIn('cleanup', str(worker))
