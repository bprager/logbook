import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from logbook.remote_agent import Outbox
from logbook.remote_config import AgentConfig


class RemoteWatchScriptTests(TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'checkout with spaces'
        (self.root / 'scripts').mkdir(parents=True)
        self.script = self.root / 'scripts' / 'remote-watch'
        shutil.copyfile(Path(__file__).resolve().parents[1] / 'scripts' / 'remote-watch', self.script)
        python = self.root / '.venv' / 'bin' / 'python'
        python.parent.mkdir(parents=True)
        python.write_text(f'#!{sys.executable}\nimport sys,json,os\nprint(json.dumps([sys.argv[1:],os.environ.get("PYTHONPATH")]))\n')
        python.chmod(0o755)
        self.env = {**os.environ, 'HOME': str(Path(tmp.name) / 'home')}
        self.env.pop('LOGBOOK_REMOTE_CONFIG', None)
        self.env.pop('PYTHONPATH', None)
        self.agent = Path(self.env['HOME']) / 'Library/Application Support/LogbookRemotePilot/remote-agent.json'

    def run_watch(self, *args):
        return subprocess.run(['bash', str(self.script), *args], cwd=self.root.parent,
                              env=self.env, capture_output=True, text=True)

    def test_mimir_uses_checkout_env_from_another_directory(self):
        (self.root / '.env').touch()
        result = self.run_watch('--once')
        self.assertEqual(result.returncode, 0, result.stderr)
        args, pythonpath = json.loads(result.stdout)
        self.assertEqual(args, ['-m', 'logbook.cli', 'watch', '--env', str(self.root / '.env'), '--once'])
        self.assertEqual(pythonpath, str(self.root / 'src'))

    def test_macbook_prefers_existing_agent_config(self):
        self.agent.parent.mkdir(parents=True)
        self.agent.touch()
        (self.root / '.env').touch()
        result = self.run_watch('--once')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0],
                         ['-m', 'logbook.remote_cli', 'watch', '--config', str(self.agent), '--once'])

    def test_explicit_modes_override_auto_detection_and_environment(self):
        self.env['LOGBOOK_REMOTE_CONFIG'] = '/configured agent.json'
        for option, module in [('--env', 'logbook.cli'), ('--config', 'logbook.remote_cli')]:
            for args in [(option, 'relative file', '--once'), (option + '=relative file', '--once')]:
                with self.subTest(args=args):
                    result = self.run_watch(*args)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout)[0], ['-m', module, 'watch', *args])

    def test_environment_config_is_not_silently_replaced(self):
        self.env['LOGBOOK_REMOTE_CONFIG'] = '/missing agent.json'
        (self.root / '.env').touch()
        result = self.run_watch('--once')
        self.assertEqual(json.loads(result.stdout)[0],
                         ['-m', 'logbook.remote_cli', 'watch', '--config', '/missing agent.json', '--once'])

    def test_missing_configuration_and_conflicting_modes_have_clear_errors(self):
        for args, message in [(('--once',), 'No watch configuration found'),
                              (('--env', 'a', '--config', 'b'), 'Choose either --env or --config')]:
            result = self.run_watch(*args)
            self.assertEqual(result.returncode, 2)
            self.assertIn(message, result.stderr)
            self.assertEqual(result.stdout, '')

    def test_help_works_without_configuration(self):
        result = self.run_watch('--help')
        self.assertEqual(result.returncode, 0)
        self.assertIn('--env', result.stdout)
        self.assertIn('--config', result.stdout)
        self.assertIn('--once', result.stdout)

    def test_real_macbook_watcher_reads_buffer_without_changing_it(self):
        self.agent.parent.mkdir(parents=True)
        token = self.agent.parent / 'token'
        token.write_text('synthetic-watch-test-token-1234567890')
        token.chmod(0o600)
        config = AgentConfig(self.agent.parent / 'buffer', self.root / 'SONY', 'SONY',
                             'test-uuid', 'REC_FILE/FOLDER01', 'America/Los_Angeles',
                             'http://127.0.0.1:18791', token, stable_seconds=0)
        self.agent.write_text(json.dumps(asdict(config), default=str))
        folder = config.mount_path / config.recordings_path
        folder.mkdir(parents=True)
        audio = folder / '261011_1200.mp3'
        audio.write_bytes(b'synthetic recording')
        box = Outbox(config)
        box.snapshot(folder)
        box.close()
        database = config.root / 'outbox.sqlite'
        before = database.read_bytes()
        script = Path(__file__).resolve().parents[1] / 'scripts' / 'remote-watch'
        result = subprocess.run(['bash', str(script), '--once'], cwd=self.root.parent,
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('buffered: 1', result.stdout)
        self.assertIn('pending: 1', result.stdout)
        self.assertEqual(database.read_bytes(), before)
        self.assertEqual(audio.read_bytes(), b'synthetic recording')
