import io
import json
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from logbook.graph_maintenance_remote import main as remote_main
from logbook.operator_entry import named_graph_recovery, release_named_processing_guard


class NamedGraphOperatorTests(TestCase):
    def test_remote_named_recovery_requires_named_profile(self):
        request={'container_id':'id','pid':1,'port':7697,'unit':'unit','private_dir':'/private',
                 'worker':'/worker','recovery_file':'/recovery','seconds':10,'profile':'named-graph',
                 'boot_guard_service':'memgraph.service','boot_guard_dir':'/var/lib/logbook-graph-guard'}
        with patch('logbook.graph_maintenance_remote.DockerRecovery'), \
             patch('logbook.graph_maintenance_remote.DockerLaunch'), \
             patch('logbook.graph_maintenance_remote.recover_named_graph_offline',return_value='rolled-back'), \
             patch('sys.stdout',new_callable=io.StringIO) as out, \
             patch('sys.argv',['remote','named-offline']):
            with patch('sys.stdin',io.StringIO(json.dumps(request))):
                remote_main()
            self.assertEqual(out.getvalue(),'rolled-back\n')
            request['profile']='other'
            with patch('sys.stdin',io.StringIO(json.dumps(request))), self.assertRaises(ValueError):
                remote_main()

    def test_local_named_actions_use_named_remote_and_require_matching_state(self):
        request={'profile':'named-graph','boot_guard_service':'memgraph.service',
                 'boot_guard_dir':'/var/lib/logbook-graph-guard','port':7697}
        state={'host':'fixture-graph','identity':'/key','entrypoint':'/entry','request':request}
        with patch('logbook.operator_entry.read_private_json',return_value=state), \
             patch('logbook.operator_entry.NamedGraphMaintenance') as remote:
            remote.return_value.invoke.return_value='rolled-back'
            self.assertEqual(named_graph_recovery(Path('/state'),'offline'),'rolled-back')
            remote.return_value.invoke.assert_called_once_with('named-offline')
            state['request']={**request,'profile':'fixture'}
            with self.assertRaises(ValueError):
                named_graph_recovery(Path('/state'),'offline')

    def test_processing_guard_stays_until_named_recovery_is_confirmed(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            from logbook.processing_restart_guard import arm_guard
            arm_guard(root,'named-window')
            config=SimpleNamespace(processing_root=root)
            remote=Mock(request={'profile':'named-graph','boot_guard_service':'memgraph.service',
                                 'boot_guard_dir':'/var/lib/logbook-graph-guard','port':7697,'unit':'named-window'})
            remote.recovered.return_value=False
            with self.assertRaisesRegex(RuntimeError,'unconfirmed'):
                release_named_processing_guard(config,remote)
            self.assertTrue((root/'graph-maintenance.pending').exists())
            remote.recovered.return_value=True
            remote.outcome.return_value='rolled-back'
            release_named_processing_guard(config,remote)
            self.assertFalse((root/'graph-maintenance.pending').exists())

    def test_remote_named_status_and_outcome_require_verified_restart(self):
        request={'container_id':'id','pid':1,'port':7697,'unit':'unit','private_dir':'/private',
                 'worker':'/worker','recovery_file':'/recovery','seconds':10,'profile':'named-graph',
                 'boot_guard_service':'memgraph.service','boot_guard_dir':'/var/lib/logbook-graph-guard'}
        with patch('logbook.graph_maintenance_remote.DockerRecovery'), \
             patch('logbook.graph_maintenance_remote.DockerLaunch'), \
             patch('logbook.graph_maintenance_remote.fixture_restarted',side_effect=[False,True,True]), \
             patch('logbook.graph_maintenance_remote.verify_named_graph_receipt',return_value={'outcome':'rolled-back'}), \
             patch('sys.stdout',new_callable=io.StringIO) as out:
            for action in ('named-status','named-status','named-outcome'):
                with patch('sys.argv',['remote',action]), patch('sys.stdin',io.StringIO(json.dumps(request))):
                    remote_main()
            self.assertEqual(out.getvalue(),'PENDING\nRECOVERED\nrolled-back\n')

    def test_remote_authorize_uses_named_verified_recovery(self):
        request={'container_id':'id','pid':1,'port':7697,'unit':'unit','private_dir':'/private',
                 'worker':'/worker','recovery_file':'/recovery','seconds':10,'profile':'named-graph',
                 'boot_guard_service':'memgraph.service','boot_guard_dir':'/var/lib/logbook-graph-guard'}
        with patch('logbook.graph_maintenance_remote.DockerRecovery'), \
             patch('logbook.graph_maintenance_remote.DockerLaunch'), \
             patch('logbook.graph_maintenance_remote.authorize_named_graph_restart') as authorize, \
             patch('sys.stdout',new_callable=io.StringIO) as out, \
             patch('sys.argv',['remote','named-authorize']), patch('sys.stdin',io.StringIO(json.dumps(request))):
            remote_main()
            authorize.assert_called_once()
            self.assertEqual(out.getvalue(),'STARTED\n')

    def test_named_remote_and_local_reject_unconfirmed_results(self):
        from logbook.graph_maintenance_cross_host import NamedGraphMaintenance
        remote=NamedGraphMaintenance('host',Path('/key'),Path('/entry'),{})
        with patch.object(remote,'invoke',side_effect=['PENDING','RECOVERED','invalid']):
            self.assertFalse(remote.recovered())
            self.assertTrue(remote.recovered())
            with self.assertRaisesRegex(RuntimeError,'not confirmed'):
                remote.outcome()
        request={'profile':'named-graph','boot_guard_service':'memgraph.service',
                 'boot_guard_dir':'/var/lib/logbook-graph-guard','port':7697}
        state={'host':'fixture-graph','identity':'/key','entrypoint':'/entry','request':request}
        with patch('logbook.operator_entry.read_private_json',return_value=state), \
             patch('logbook.operator_entry.NamedGraphMaintenance') as cls:
            cls.return_value.invoke.return_value='invalid'
            with self.assertRaisesRegex(RuntimeError,'unconfirmed'):
                named_graph_recovery(Path('/state'),'offline')
            with self.assertRaisesRegex(RuntimeError,'unconfirmed'):
                named_graph_recovery(Path('/state'),'authorize')
            cls.return_value.recovered.return_value=False
            self.assertEqual(named_graph_recovery(Path('/state'),'status'),'pending')
            cls.return_value.recovered.return_value=True
            cls.return_value.outcome.return_value='retained'
            self.assertEqual(named_graph_recovery(Path('/state'),'status'),'retained')
            cls.return_value.invoke.return_value='STARTED'
            self.assertEqual(named_graph_recovery(Path('/state'),'authorize'),'started')
            with self.assertRaises(ValueError):
                named_graph_recovery(Path('/state'),'invalid')

    def test_status_fails_closed_on_remote_verification_error(self):
        request={'container_id':'id','pid':1,'port':7697,'unit':'unit','private_dir':'/private',
                 'worker':'/worker','recovery_file':'/recovery','seconds':10,'profile':'named-graph',
                 'boot_guard_service':'memgraph.service','boot_guard_dir':'/var/lib/logbook-graph-guard'}
        with patch('logbook.graph_maintenance_remote.DockerRecovery'), \
             patch('logbook.graph_maintenance_remote.DockerLaunch'), \
             patch('logbook.graph_maintenance_remote.fixture_restarted',side_effect=ValueError('bad')), \
             patch('sys.stdout',new_callable=io.StringIO) as out, \
             patch('sys.argv',['remote','named-outcome']), patch('sys.stdin',io.StringIO(json.dumps(request))):
            remote_main()
            self.assertEqual(out.getvalue(),'PENDING\n')

    def test_named_outcome_and_release_validation(self):
        from logbook.graph_maintenance_cross_host import NamedGraphMaintenance
        remote=NamedGraphMaintenance('host',Path('/key'),Path('/entry'),{})
        with patch.object(remote,'invoke',return_value='rolled-back'):
            self.assertEqual(remote.outcome(),'rolled-back')
        config=SimpleNamespace(processing_root=Path('/unused'))
        with self.assertRaisesRegex(ValueError,'state'):
            release_named_processing_guard(config,Mock(request={'profile':'wrong'}))
        request={'profile':'named-graph','boot_guard_service':'memgraph.service',
                 'boot_guard_dir':'/var/lib/logbook-graph-guard','port':7697}
        with self.assertRaisesRegex(ValueError,'unit'):
            release_named_processing_guard(config,Mock(request=request))
