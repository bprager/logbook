import json
import runpy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch


class RemotePilotInstallerTests(TestCase):
    def test_installs_only_separate_pilot_and_refuses_existing_services(self):
        installer = runpy.run_path(str(Path(__file__).resolve().parents[1] /
                                      'scripts/install-remote-pilot.py'))['main']
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            pilot = home / 'Library/Application Support/LogbookRemotePilot'
            pilot.mkdir(parents=True)
            settings = pilot / 'remote-agent.json'
            token = home / 'token'
            token.write_text('x' * 40)
            token.chmod(0o600)
            config = dict(root=str(pilot), mount_path='/Volumes/IC RECORDER',
                          volume_name='IC RECORDER', volume_uuid='uuid', recordings_path='REC_FILE/FOLDER01',
                          timezone='America/Los_Angeles', server_url='http://127.0.0.1:18791',
                          token_file=str(token))
            settings.write_text(json.dumps(config))
            def package(config, settings, output, repo, identity):
                output.mkdir(parents=True)
                for name in ('remote-tunnel', 'remote-agent'):
                    (output / f'local.logbook.{name}.plist').write_text('test plist')
            with patch('pathlib.Path.home', return_value=home), \
                    patch('logbook.remote_launchd.write_agent_package', side_effect=package) as build, \
                    patch('subprocess.run') as run:
                installer(['--signing-identity', 'Local Test'])
                self.assertEqual(build.call_args.args[0].ssh_host, 'jump-mimir')
                self.assertEqual(build.call_args.args[0].ssh_remote_port, 18791)
                self.assertEqual(run.call_count, 2)
                self.assertTrue(all(call.args[0][1] == 'bootstrap' for call in run.call_args_list))
                with self.assertRaises(SystemExit):
                    installer(['--signing-identity', 'Local Test'])
            self.assertEqual(token.read_text(), 'x' * 40)

    def test_rejects_production_buffer_before_writing(self):
        installer = runpy.run_path(str(Path(__file__).resolve().parents[1] /
                                      'scripts/install-remote-pilot.py'))['main']
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            pilot = home / 'Library/Application Support/LogbookRemotePilot'
            pilot.mkdir(parents=True)
            settings = pilot / 'remote-agent.json'
            settings.write_text(json.dumps({'root': str(home / 'production')}))
            before = settings.read_bytes()
            with patch('pathlib.Path.home', return_value=home), patch('subprocess.run') as run:
                with self.assertRaises(SystemExit):
                    installer(['--signing-identity', 'Local Test'])
                run.assert_not_called()
            self.assertEqual(settings.read_bytes(), before)
