import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from logbook.operator_entry import run_fixture


class OperatorEntryTests(unittest.TestCase):
    def test_provisions_and_records_recovery_information_before_window(self):
        with TemporaryDirectory() as tmp:
            state = Path(tmp)/'state.json'
            options = {'host':'odin', 'identity':'/key', 'remote_parent':'/private',
                       'journal_dir':'/private/journals', 'container_id':'a'*64, 'pid':123,
                       'port':7687, 'seconds':15}
            remote = Mock()
            def run(config, job_id, candidate, *, timeout_seconds):
                self.assertTrue(state.exists())
                saved = json.loads(state.read_text())
                self.assertEqual(saved['request']['container_id'], options['container_id'])
                self.assertTrue(saved['request']['sandbox_only'])
                return 'retained'
            with patch('logbook.operator_entry.build_installed_bundle', return_value=b'archive'), \
                 patch('logbook.operator_entry.provision_bundle') as provision, \
                 patch('logbook.operator_entry.SshMaintenance', return_value=remote), \
                 patch('logbook.operator_entry.stage_plan_and_run', side_effect=run):
                self.assertEqual(run_fixture(Mock(), 147, options, state), 'retained')
            provision.assert_called_once()
            self.assertEqual(state.stat().st_mode & 0o777, 0o600)

    def test_bundle_uses_installed_packages(self):
        from logbook.operator_entry import build_installed_bundle
        with patch('logbook.operator_entry.build_bundle', return_value=b'fixture') as build:
            self.assertEqual(build_installed_bundle(), b'fixture')
            self.assertEqual(set(build.call_args.args[0]), {'logbook','neo4j','pytz'})
