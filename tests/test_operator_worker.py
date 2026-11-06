import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from logbook.graph_maintenance_worker import load_plan, read_private_json


class OperatorWorkerTests(unittest.TestCase):
    def test_reads_private_plan_and_rejects_unprotected_inputs(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / 'plan.json'
            plan.write_text(json.dumps({'nodes': [{'id':'logbook:job:000123',
                'labels':['LogbookJob'], 'properties':{}}], 'relationships':[], 'generated_at':'now'}))
            plan.chmod(0o600)
            loaded = load_plan(plan)
            self.assertEqual(loaded.nodes[0].labels, ('LogbookJob',))
            plan.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'private'):
                read_private_json(plan)
            plan.chmod(0o600)
            link = root / 'link'
            link.symlink_to(plan)
            with self.assertRaises(OSError):
                read_private_json(link)

    def test_worker_requires_root_and_fence_for_apply_and_recovery(self):
        from unittest.mock import MagicMock, patch
        from logbook.graph_maintenance_worker import run_worker
        request = {'journal':'/private/journal','plan':'/private/plan',
                   'container_id':'a'*64,'pid':123,'port':7687,
                   'unit':'logbook-graph-maintenance-test.service'}
        with patch('logbook.graph_maintenance_worker.os.geteuid', return_value=1):
            with self.assertRaisesRegex(ValueError, 'root'):
                run_worker('apply', Path('/private/request'))
        driver = MagicMock()
        with patch('logbook.graph_maintenance_worker.os.geteuid', return_value=0), \
             patch('logbook.graph_maintenance_worker.read_private_json', side_effect=[request, request, {'batch':'batch'}]), \
             patch('logbook.graph_maintenance_worker.private_directory'), \
             patch('logbook.graph_maintenance_worker.DockerRecovery') as target, \
             patch('logbook.graph_maintenance_worker.GraphWriteLock'), \
             patch('logbook.graph_maintenance_worker.load_plan'), \
             patch('logbook.graph_maintenance_worker.Neo4jMemgraphClient'), \
             patch('logbook.graph_maintenance_worker.repair_in_window') as apply, \
             patch('logbook.graph_maintenance_worker.recover_in_window') as recover, \
             patch('neo4j.GraphDatabase.driver', return_value=driver):
            run_worker('apply', Path('/private/request'))
            run_worker('recover', Path('/private/request'))
            self.assertEqual(target.return_value.verify_target.call_count, 2)
            apply.assert_called_once()
            recover.assert_called_once()

    def test_private_input_must_be_object_in_private_directory(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root/'data'
            data.write_text('[]')
            data.chmod(0o600)
            with self.assertRaisesRegex(ValueError, 'object'):
                read_private_json(data)
            root.chmod(0o755)
            with self.assertRaisesRegex(ValueError, 'directory'):
                read_private_json(data)
            root.chmod(0o700)
