import unittest
from unittest.mock import Mock

from logbook.fixture_boot_guard import verify_fixture_binding


class FixtureServiceBindingTests(unittest.TestCase):
    def test_matches_service_process_to_named_container(self):
        recovery = Mock(container_id='a'*64, pid=101)
        recovery.run.side_effect = [
            '4321\n', '/usr/bin/docker\n',
            '/usr/bin/docker\0run\0--name\0logbook-fixture-graph\0image\0',
            'a'*64+' /logbook-fixture-graph 101 true\n', '4321\n',
        ]
        verify_fixture_binding(recovery, 'logbook-fixture-graph.service')

    def test_rejects_different_container_or_service_process(self):
        for bad in ('/usr/bin/docker\0run\0--name\0logbook-fixture-other\0image\0',
                    '/usr/bin/docker\0start\0logbook-fixture-graph\0'):
            recovery = Mock(container_id='a'*64, pid=101)
            recovery.run.side_effect = ['4321\n', '/usr/bin/docker\n', bad]
            with self.assertRaisesRegex(ValueError, 'binding'):
                verify_fixture_binding(recovery, 'logbook-fixture-graph.service')
        recovery = Mock(container_id='a'*64, pid=101)
        recovery.run.side_effect = ['4321\n', '/usr/bin/docker\n',
                                    '/usr/bin/docker\0run\0--name\0logbook-fixture-graph\0image\0',
                                    'b'*64+' /logbook-fixture-graph 101 true\n']
        with self.assertRaisesRegex(ValueError, 'binding'):
            verify_fixture_binding(recovery, 'logbook-fixture-graph.service')

    def test_rejects_invalid_unit_missing_pid_and_process_change(self):
        recovery = Mock(container_id='a'*64, pid=101)
        with self.assertRaisesRegex(ValueError, 'fixture'):
            verify_fixture_binding(recovery, 'memgraph.service')
        recovery.run.return_value = '0\n'
        with self.assertRaisesRegex(ValueError, 'binding'):
            verify_fixture_binding(recovery, 'logbook-fixture-graph.service')
        recovery.run.side_effect = ['4321\n', '/usr/bin/docker\n',
                                    '/usr/bin/docker\0run\0--name\0logbook-fixture-graph\0image\0',
                                    'a'*64+' /logbook-fixture-graph 101 true\n', '4322\n']
        with self.assertRaisesRegex(ValueError, 'changed'):
            verify_fixture_binding(recovery, 'logbook-fixture-graph.service')
