import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from logbook.fixture_offline_recovery import record_fixture_storage, recover_offline


class FixtureOfflineTests(unittest.TestCase):
    def test_records_original_fixture_volume_before_restart(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            recovery = Mock(container_id='a'*64, pid=12)
            recovery.run.side_effect = [json.dumps([{'Type':'volume','Name':'logbook-fixture-data',
                                                    'Destination':'/var/lib/memgraph','RW':True}]),
                                        'sha256:'+'b'*64]
            request = {'private_dir':str(root),'boot_guard_service':'logbook-fixture-graph.service',
                       'boot_guard_dir':str(root),'journal':str(root/'journal'),
                       'unit':'logbook-graph-maintenance-test.service','port':7697,'recovery_file':'/bundle/logbook/recovery.py'}
            with patch('logbook.fixture_offline_recovery.boot_id', return_value='boot-one'):
                record_fixture_storage(request, recovery)
            record = json.loads((root/'boot-target.json').read_text())
            self.assertEqual(record['volume'], 'logbook-fixture-data')
            self.assertEqual(record['container'], 'a'*64)
            self.assertEqual(record['boot_id'], 'boot-one')

    def test_rejects_storage_that_is_not_a_fixture_volume(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            recovery = Mock(container_id='a'*64, pid=12)
            recovery.run.return_value = json.dumps([{'Type':'volume','Name':'production',
                                                      'Destination':'/var/lib/memgraph','RW':True}])
            with self.assertRaisesRegex(ValueError, 'fixture volume'):
                record_fixture_storage({'private_dir':str(root)}, recovery)
            self.assertFalse((root/'boot-target.json').exists())

    def test_refuses_recovery_before_reboot_or_when_marker_exists(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = {'private_dir':str(root),'boot_guard_service':'logbook-fixture-graph.service',
                       'boot_guard_dir':str(root),'journal':str(root/'journal'),
                       'unit':'logbook-graph-maintenance-test.service', 'container_id':'a'*64, 'pid':12,
                       'port':7697, 'recovery_file':'/bundle/logbook/recovery.py'}
            record = {'container':'a'*64,'pid':12,'unit':request['unit'],
                      'service':request['boot_guard_service'],'guard_dir':str(root),
                      'journal':request['journal'],'port':7697,'recovery_file':request['recovery_file'],'volume':'logbook-fixture-data',
                      'image':'sha256:'+'b'*64,'boot_id':'boot-one'}
            path = root/'boot-target.json'
            path.write_text(json.dumps(record))
            path.chmod(0o600)
            with patch('logbook.fixture_offline_recovery.boot_id', return_value='boot-one'):
                with self.assertRaisesRegex(ValueError, 'restart'):
                    recover_offline(request, Mock())
            (root/'start-allowed').write_text('allowed')
            with patch('logbook.fixture_offline_recovery.boot_id', return_value='boot-two'):
                with self.assertRaisesRegex(ValueError, 'guard'):
                    recover_offline(request, Mock())

    def make_state(self, root):
        request = {'private_dir':str(root), 'boot_guard_service':'logbook-fixture-graph.service',
                   'boot_guard_dir':str(root), 'journal':str(root/'journal'),
                   'unit':'logbook-graph-maintenance-test.service', 'container_id':'a'*64, 'pid':12,
                   'port':7697, 'recovery_file':'/bundle/logbook/recovery.py'}
        record = {'container':request['container_id'], 'pid':12, 'unit':request['unit'],
                  'service':request['boot_guard_service'], 'guard_dir':str(root),
                  'journal':request['journal'], 'port':7697, 'recovery_file':request['recovery_file'],
                  'volume':'logbook-fixture-data', 'image':'sha256:'+'b'*64, 'boot_id':'boot-one'}
        path = root/'boot-target.json'
        path.write_text(json.dumps(record))
        path.chmod(0o600)
        journal = root/'journal'
        journal.write_text(json.dumps({'schema':1, 'batch':'00000000-0000-0000-0000-000000000001'}))
        journal.chmod(0o600)
        return request

    def test_offline_recovery_isolated_then_records_outcome_and_keeps_guard_closed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = self.make_state(root)
            recovery = Mock()
            recovery.run.side_effect = ['', 'container-id', '321 true none',
                                        json.dumps([{'Destination':'/var/lib/memgraph','Type':'volume',
                                                     'Name':'logbook-fixture-data','RW':True}]),
                                        'rolled-back', '', '']
            with patch('logbook.fixture_offline_recovery.boot_id', return_value='boot-two'), \
                 patch('logbook.fixture_offline_recovery.FixtureBootGuard') as guard:
                guard.return_value.marker = root/'start-allowed'
                self.assertEqual(recover_offline(request, recovery), 'rolled-back')
                guard.return_value.verify.assert_called_once_with('inactive')
            self.assertEqual(json.loads((root/'offline-outcome.json').read_text())['outcome'], 'rolled-back')
            commands = [call.args[0] for call in recovery.run.call_args_list]
            self.assertIn('--network=none', commands[1])
            self.assertEqual(commands[5][:3], ['docker', 'stop', '--time'])
            self.assertFalse((root/'start-allowed').exists())

    def test_busy_volume_or_failed_rollback_does_not_create_outcome(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = self.make_state(root)
            with patch('logbook.fixture_offline_recovery.boot_id', return_value='boot-two'), \
                 patch('logbook.fixture_offline_recovery.FixtureBootGuard') as guard:
                guard.return_value.marker = root/'start-allowed'
                recovery = Mock()
                recovery.run.return_value = 'other-container'
                with self.assertRaisesRegex(ValueError, 'in use'):
                    recover_offline(request, recovery)
                recovery.run.reset_mock()
                recovery.run.side_effect = ['', 'container-id', '321 true none',
                                            json.dumps([{'Destination':'/var/lib/memgraph','Type':'volume',
                                                         'Name':'logbook-fixture-data','RW':True}]),
                                            RuntimeError('rollback failed'), '']
                with self.assertRaisesRegex(RuntimeError, 'rollback failed'):
                    recover_offline(request, recovery)
                self.assertEqual(recovery.run.call_args.args[0][:2], ['docker', 'stop'])
            self.assertFalse((root/'offline-outcome.json').exists())

    def test_rejects_changed_target_bad_storage_completion_and_duplicate(self):
        from logbook.fixture_offline_recovery import boot_id
        with patch('logbook.fixture_offline_recovery.Path.read_text', return_value=' boot-id\n'):
            self.assertEqual(boot_id(), 'boot-id')
        for scenario in ('target', 'completion', 'journal', 'duplicate', 'storage'):
            with self.subTest(scenario=scenario), TemporaryDirectory() as tmp:
                root = Path(tmp)
                request = self.make_state(root)
                target = root/'boot-target.json'
                record = json.loads(target.read_text())
                if scenario == 'target':
                    record['pid'] = 999
                if scenario == 'storage':
                    record['volume'] = 'production'
                target.write_text(json.dumps(record))
                if scenario == 'completion':
                    (root/'journal.complete').touch()
                if scenario == 'journal':
                    (root/'journal').write_text(json.dumps({'schema':0,'batch':'00000000-0000-0000-0000-000000000001'}))
                if scenario == 'duplicate':
                    (root/'offline-outcome.json').touch()
                with patch('logbook.fixture_offline_recovery.boot_id', return_value='boot-two'), \
                     patch('logbook.fixture_offline_recovery.FixtureBootGuard') as guard:
                    guard.return_value.marker = root/'start-allowed'
                    with self.assertRaises(ValueError):
                        recover_offline(request, Mock())

    def test_rejects_wrong_image_and_unexpected_recovery_container(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            recovery = Mock(container_id='a'*64, pid=12)
            recovery.run.side_effect = [json.dumps([{'Type':'volume','Name':'logbook-fixture-data',
                                                     'Destination':'/var/lib/memgraph','RW':True}]), 'bad-image']
            with self.assertRaisesRegex(ValueError, 'image'):
                record_fixture_storage({'private_dir':str(root)}, recovery)
        for scenario in ('network', 'mount', 'outcome', 'remaining'):
            with self.subTest(scenario=scenario), TemporaryDirectory() as tmp:
                root = Path(tmp)
                request = self.make_state(root)
                recovery = Mock()
                mount = json.dumps([{'Destination':'/var/lib/memgraph','Type':'volume',
                                     'Name':'logbook-fixture-data','RW':True}])
                if scenario == 'network':
                    responses = ['', 'id', '321 true bridge', '']
                elif scenario == 'mount':
                    responses = ['', 'id', '321 true none', '[]', '']
                elif scenario == 'outcome':
                    responses = ['', 'id', '321 true none', mount, 'unknown', '']
                else:
                    responses = ['', 'id', '321 true none', mount, 'rolled-back', '', 'still-running']
                recovery.run.side_effect = responses
                with patch('logbook.fixture_offline_recovery.boot_id', return_value='boot-two'), \
                     patch('logbook.fixture_offline_recovery.FixtureBootGuard') as guard:
                    guard.return_value.marker = root/'start-allowed'
                    with self.assertRaises(ValueError):
                        recover_offline(request, recovery)
                self.assertFalse((root/'offline-outcome.json').exists())

    def test_completed_fixture_requires_matching_receipt_and_retained_result(self):
        from logbook.graph_maintenance_worker import completion_value
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = self.make_state(root)
            batch = '00000000-0000-0000-0000-000000000001'
            completion = root/'journal.complete'
            completion.write_text(json.dumps(completion_value(request,batch)))
            completion.chmod(0o600)
            mount = json.dumps([{'Destination':'/var/lib/memgraph','Type':'volume',
                                 'Name':'logbook-fixture-data','RW':True}])
            recovery = Mock()
            recovery.run.side_effect = ['', 'id', '321 true none', mount, 'retained', '', '']
            with patch('logbook.fixture_offline_recovery.boot_id', return_value='boot-two'), \
                 patch('logbook.fixture_offline_recovery.FixtureBootGuard') as guard:
                guard.return_value.marker = root/'start-allowed'
                self.assertEqual(recover_offline(request, recovery), 'retained')
            self.assertEqual(recovery.run.call_args_list[4].args[0][-1], 'retain')
            self.assertEqual(json.loads((root/'offline-outcome.json').read_text())['outcome'], 'retained')
            (root/'offline-outcome.json').unlink()
            completion.write_text(json.dumps({**completion_value(request,batch),'pid':999}))
            with patch('logbook.fixture_offline_recovery.boot_id', return_value='boot-two'), \
                 patch('logbook.fixture_offline_recovery.FixtureBootGuard') as guard:
                guard.return_value.marker = root/'start-allowed'
                with self.assertRaisesRegex(ValueError, 'completion'):
                    recover_offline(request, Mock())
