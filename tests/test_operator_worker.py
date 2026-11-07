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
             patch('logbook.graph_maintenance_worker.write_outcome'), \
             patch('logbook.graph_maintenance_worker.DockerRecovery') as target, \
             patch('logbook.graph_maintenance_worker.GraphWriteLock'), \
             patch('logbook.graph_maintenance_worker.load_plan'), \
             patch('logbook.graph_maintenance_worker.Neo4jMemgraphClient'), \
             patch('logbook.graph_maintenance_worker.repair_in_window') as apply, \
             patch('logbook.graph_maintenance_worker.rollback_batch') as verify, \
             patch('logbook.graph_maintenance_worker.write_completion') as receipt, \
             patch('logbook.graph_maintenance_worker.recover_in_window') as recover, \
             patch('neo4j.GraphDatabase.driver', return_value=driver):
            run_worker('apply', Path('/private/request'))
            run_worker('recover', Path('/private/request'))
            self.assertEqual(target.return_value.verify_target.call_count, 2)
            apply.assert_called_once()
            verify.assert_called_once()
            receipt.assert_called_once()
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

    def test_completion_receipt_is_bound_to_batch_and_target(self):
        from logbook.graph_maintenance_worker import completion_matches, write_completion
        request = {'container_id':'a'*64, 'pid':123, 'port':7687, 'unit':'fixture'}
        with TemporaryDirectory() as tmp:
            journal = Path(tmp)/'journal'
            write_completion(journal, request, 'batch')
            self.assertTrue(completion_matches(journal, request, 'batch'))
            self.assertFalse(completion_matches(journal, request, 'other'))
            with self.assertRaises(FileExistsError):
                write_completion(journal, request, 'batch')

    def test_completion_rechecks_batch_and_rejects_wrong_receipt(self):
        from unittest.mock import MagicMock, patch
        from logbook.graph_maintenance_worker import run_worker
        request = {'journal':'/private/journal', 'container_id':'a'*64, 'pid':123,
                   'port':7687, 'unit':'logbook-graph-maintenance-test.service'}
        with patch('logbook.graph_maintenance_worker.os.geteuid', return_value=0), \
             patch('logbook.graph_maintenance_worker.read_private_json', side_effect=[request, {'batch':'batch'}, request, {'batch':'batch'}]), \
             patch('logbook.graph_maintenance_worker.private_directory'), \
             patch('logbook.graph_maintenance_worker.write_outcome'), \
             patch('logbook.graph_maintenance_worker.DockerRecovery'), \
             patch('logbook.graph_maintenance_worker.GraphWriteLock'), \
             patch('logbook.graph_maintenance_worker.os.path.lexists', return_value=True), \
             patch('logbook.graph_maintenance_worker.completion_matches', side_effect=[True, False]), \
             patch('logbook.graph_maintenance_worker.rollback_batch') as verify, \
             patch('neo4j.GraphDatabase.driver', return_value=MagicMock()):
            run_worker('recover', Path('/private/request'))
            with self.assertRaisesRegex(ValueError, 'does not match'):
                run_worker('recover', Path('/private/request'))
            verify.assert_called_once()

    def test_outcome_is_durable_and_retry_preserves_known_rollback(self):
        from logbook.graph_maintenance_worker import write_outcome
        request = {'container_id':'a'*64, 'pid':123, 'port':7687, 'unit':'fixture'}
        with TemporaryDirectory() as tmp:
            journal = Path(tmp)/'journal'
            write_outcome(journal, request, 'batch', 'rolled-back')
            write_outcome(journal, request, 'batch', 'not-retained')
            result = read_private_json(Path(str(journal)+'.outcome'))
            self.assertEqual(result['outcome'], 'rolled-back')
            with self.assertRaisesRegex(ValueError, 'outcome'):
                write_outcome(journal, request, 'other', 'retained')

    def test_unknown_outcome_is_rejected(self):
        from logbook.graph_maintenance_worker import write_outcome
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'invalid outcome'):
                write_outcome(Path(tmp)/'journal', {}, 'batch', 'unknown')
