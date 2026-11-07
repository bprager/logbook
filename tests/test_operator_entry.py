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
                       'port':7687, 'seconds':15, 'revision':'a'*40, 'wheel_dir':'/wheels',
                       'boot_guard_dir':'/guard', 'boot_guard_service':'logbook-fixture-graph.service'}
            remote = Mock()
            def run(config, job_id, candidate, *, timeout_seconds):
                self.assertTrue(state.exists())
                saved = json.loads(state.read_text())
                self.assertEqual(saved['request']['container_id'], options['container_id'])
                self.assertTrue(saved['request']['sandbox_only'])
                self.assertEqual(saved['revision'], options['revision'])
                self.assertEqual(len(saved['bundle_sha256']), 64)
                return 'retained'
            with patch('logbook.operator_entry.verify_checkout'), \
                 patch('logbook.operator_entry.build_release_bundle', return_value=b'archive'), \
                 patch('logbook.operator_entry.provision_bundle') as provision, \
                 patch('logbook.operator_entry.SshMaintenance', return_value=remote), \
                 patch('logbook.operator_entry.stage_plan_and_run', side_effect=run):
                self.assertEqual(run_fixture(Mock(), 147, options, state), 'retained')
            provision.assert_called_once()
            self.assertEqual(state.stat().st_mode & 0o777, 0o600)

    def test_bundle_uses_installed_packages(self):
        from logbook.operator_entry import build_release_bundle
        with patch('logbook.operator_entry.build_revision_bundle', return_value=b'fixture') as build:
            self.assertEqual(build_release_bundle('a'*40, Path('/wheels')), b'fixture')
            self.assertEqual(build.call_args.args[2], Path('/wheels'))

    def test_saved_fixture_can_retry_and_report_confirmed_status(self):
        from logbook.operator_entry import fixture_recovery
        with TemporaryDirectory() as tmp:
            state = Path(tmp)/'state.json'
            state.write_text(json.dumps({'host':'odin', 'identity':'/key', 'entrypoint':'/entry',
                                         'request':{'sandbox_only':True}}))
            state.chmod(0o600)
            remote = Mock()
            remote.recovered.side_effect = [False, True]
            remote.outcome.return_value = 'rolled-back'
            with patch('logbook.operator_entry.SshMaintenance', return_value=remote):
                self.assertEqual(fixture_recovery(state, 'retry'), 'retry-started')
                self.assertEqual(fixture_recovery(state, 'status'), 'pending')
                self.assertEqual(fixture_recovery(state, 'status'), 'rolled-back')
                remote.retry.assert_called_once()
                remote.invoke.return_value = 'rolled-back'
                self.assertEqual(fixture_recovery(state, 'offline'), 'rolled-back')
                remote.invoke.return_value = 'unknown'
                with self.assertRaisesRegex(RuntimeError, 'unconfirmed'):
                    fixture_recovery(state, 'offline')
                with self.assertRaisesRegex(ValueError, 'expected'):
                    fixture_recovery(state, 'other')
            state.write_text(json.dumps({'host':'odin', 'identity':'/key', 'entrypoint':'/entry',
                                         'request':{'sandbox_only':False}}))
            with self.assertRaisesRegex(ValueError, 'fixture'):
                fixture_recovery(state, 'retry')
            state.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'private'):
                fixture_recovery(state, 'status')
