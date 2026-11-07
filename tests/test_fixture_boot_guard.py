import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from logbook.fixture_boot_guard import FixtureBootGuard


class FixtureBootGuardTests(unittest.TestCase):
    def test_removes_start_permission_before_work_and_restores_after_confirmation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root/'start-allowed'
            marker.write_text('allowed\n')
            marker.chmod(0o600)
            run = Mock(side_effect=['loaded\nactive\n', json.dumps({'type':'a(sbbsi)', 'data':[['ConditionPathExists', False, False, str(marker), 1]]})]*3)
            guard = FixtureBootGuard(root, 'logbook-fixture-graph.service', run)
            guard.disarm()
            self.assertFalse(marker.exists())
            with self.assertRaises(FileNotFoundError):
                guard.disarm()
            guard.allow()
            self.assertEqual(marker.read_text(), 'allowed\n')

    def test_rejects_unrelated_or_unprotected_service(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, 'fixture'):
                FixtureBootGuard(root, 'memgraph.service', Mock())
            marker = root/'start-allowed'
            marker.write_text('allowed\n')
            marker.chmod(0o600)
            guard = FixtureBootGuard(root, 'logbook-fixture-graph.service', Mock(side_effect=['loaded\nactive\n', json.dumps({'type':'a(sbbsi)', 'data':[]})]))
            with self.assertRaisesRegex(ValueError, 'condition'):
                guard.disarm()
            self.assertTrue(marker.exists())

    def test_refuses_bad_marker_and_inactive_service(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root/'start-allowed'
            marker.write_text('not allowed')
            marker.chmod(0o600)
            condition = json.dumps({'type':'a(sbbsi)', 'data':[['ConditionPathExists', False, False, str(marker), 1]]})
            guard = FixtureBootGuard(root, 'logbook-fixture-graph.service',
                                     Mock(side_effect=['loaded\nactive\n', condition]))
            with self.assertRaisesRegex(ValueError, 'marker'):
                guard.disarm()
            self.assertTrue(marker.exists())
            guard = FixtureBootGuard(root, 'logbook-fixture-graph.service', Mock(return_value='loaded\ninactive\n'))
            with self.assertRaisesRegex(ValueError, 'active'):
                guard.disarm()
            root.chmod(0o755)
            with self.assertRaisesRegex(ValueError, 'private'):
                guard.disarm()
            root.chmod(0o700)

    def test_allow_is_idempotent_only_for_valid_marker(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root/'start-allowed'
            marker.write_text('allowed\n')
            marker.chmod(0o600)
            condition = json.dumps({'type':'a(sbbsi)', 'data':[['ConditionPathExists', False, False, str(marker), 1]]})
            guard = FixtureBootGuard(root, 'logbook-fixture-graph.service',
                                     Mock(side_effect=['loaded\nactive\n', condition]*2))
            guard.allow()
            marker.write_text('invalid')
            with self.assertRaisesRegex(ValueError, 'marker'):
                guard.allow()
