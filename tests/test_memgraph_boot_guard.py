import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from logbook.fixture_boot_guard import MemgraphBootGuard, verify_memgraph_binding


class MemgraphBootGuardTests(TestCase):
    def test_production_named_guard_stops_and_restores_start_permission(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / 'start-allowed'
            marker.write_text('allowed\n')
            marker.chmod(0o600)
            conditions = json.dumps({'type':'a(sbbsi)', 'data':[
                ['ConditionPathExists', False, False, str(marker), 1]]})
            run = Mock(side_effect=['loaded\nactive\n', conditions, 'loaded\ninactive\n', conditions])
            guard = MemgraphBootGuard(root, run)
            self.assertEqual(guard.service, 'memgraph.service')
            guard.disarm()
            self.assertFalse(marker.exists())
            guard.allow('inactive')
            self.assertEqual(marker.read_text(), 'allowed\n')

    def test_binding_requires_the_real_service_and_container_identity(self):
        recovery = Mock(container_id='a'*64, pid=123)
        recovery.run.side_effect = ['456', '/usr/bin/docker',
            '/usr/bin/docker\0run\0--name\0memgraph-service\0',
            f'{"a"*64} /memgraph-service 123 true', '456']
        verify_memgraph_binding(recovery)
        recovery.run.side_effect = ['456', '/usr/bin/docker',
            '/usr/bin/docker\0run\0--name\0other\0']
        with self.assertRaisesRegex(ValueError, 'binding'):
            verify_memgraph_binding(recovery)
