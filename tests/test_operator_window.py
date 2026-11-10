import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from logbook.operator_window import stage_plan_and_run


class OperatorWindowTests(unittest.TestCase):
    def test_prepares_stages_and_uses_existing_coordinator(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = Mock()
            config = SimpleNamespace(processing_root=root)
            def prepare(config, job_id, path):
                path.write_text(json.dumps({'nodes':[], 'relationships':[], 'generated_at':'test'}))
                path.chmod(0o600)
                return (0, 0)
            with patch('logbook.operator_window.prepare_job_plan', side_effect=prepare), \
                 patch('logbook.operator_window.coordinate_leased_window') as coordinate:
                stage_plan_and_run(config, 123, remote, timeout_seconds=30)
            remote.stage.assert_called_once()
            coordinate.assert_called_once_with(config, remote, timeout_seconds=30)

    def test_stage_failure_does_not_launch(self):
        remote = Mock()
        remote.stage.side_effect = RuntimeError('rejected')
        with patch('logbook.operator_window.prepare_job_plan') as prepare, \
             patch('logbook.operator_window.read_private_json', return_value={}), \
             patch('logbook.operator_window.coordinate_leased_window') as coordinate:
            with self.assertRaisesRegex(RuntimeError, 'rejected'):
                stage_plan_and_run(Mock(), 123, remote, timeout_seconds=30)
        self.assertTrue(prepare.called)
        coordinate.assert_not_called()

    def test_remote_stage_creates_private_files_without_overwriting(self):
        import os
        from logbook.graph_maintenance_stage import stage_plan
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root/'bundle'
            bundle.mkdir(mode=0o700)
            private = root/'private'
            private.mkdir(mode=0o700)
            journal_dir = root/'journal'
            journal_dir.mkdir(mode=0o700)
            request = {'container_id':'a'*64,'pid':123,'port':7687,
                       'unit':'logbook-graph-maintenance-'+'a'*32+'.service',
                       'private_dir':str(private), 'journal':str(journal_dir/'batch.json'),
                       'worker':str(private/'worker'), 'recovery_worker':str(private/'restore'),
                       'recovery_file':str(bundle/'logbook/graph_maintenance_recovery.py'),
                       'seconds':10, 'profile':'named-graph', 'plan_proof':{'signature':'fixture'},
                       'plan_data':{'nodes':[],'relationships':[],'generated_at':'test'}}
            stage_plan(request, bundle)
            self.assertEqual((private/'plan.json').stat().st_mode & 0o777, 0o600)
            self.assertEqual((private/'worker').stat().st_mode & 0o777, 0o700)
            self.assertEqual(json.loads((private/'plan-proof.json').read_text()), request['plan_proof'])
            self.assertEqual((private/'plan-proof.json').stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads((private/'request.json').read_text())['journal'], request['journal'])
            with self.assertRaises(FileExistsError):
                stage_plan(request, bundle)
            self.assertEqual(os.stat(private).st_mode & 0o777, 0o700)

    def test_remote_stage_rejects_wrong_paths_and_large_plan(self):
        from logbook.graph_maintenance_stage import stage_plan
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root/'bundle'
            private = root/'private'
            journal = root/'journal'
            for path in (bundle, private, journal):
                path.mkdir(mode=0o700)
            request = {'private_dir':str(private), 'journal':str(journal/'record'),
                       'worker':'/wrong', 'recovery_worker':str(private/'restore'),
                       'recovery_file':str(bundle/'logbook/graph_maintenance_recovery.py'), 'plan_data':{}}
            with self.assertRaisesRegex(ValueError, 'paths'):
                stage_plan(request, bundle)
            request['worker'] = str(private/'worker')
            request['plan_data'] = {'payload':'x'*5000001}
            with self.assertRaisesRegex(ValueError, 'bounded'):
                stage_plan(request, bundle)

    def test_operator_returns_confirmed_batch_outcome(self):
        with patch('logbook.operator_window.prepare_job_plan'), \
             patch('logbook.operator_window.read_private_json', return_value={}), \
             patch('logbook.operator_window.coordinate_leased_window'):
            remote = Mock()
            remote.outcome.return_value = 'rolled-back'
            self.assertEqual(stage_plan_and_run(Mock(), 123, remote, timeout_seconds=30), 'rolled-back')
