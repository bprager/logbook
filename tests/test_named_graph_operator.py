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
    def test_release_package_reader_rejects_unsafe_or_changed_files(self):
        import os
        from types import SimpleNamespace
        from logbook.operator_entry import read_release_bundle
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root/'release'
            data.write_bytes(b'fixture')
            self.assertEqual(read_release_bundle(data),b'fixture')
            with self.assertRaisesRegex(ValueError,'absolute'):
                read_release_bundle(Path('relative'))
            data.write_bytes(b'')
            with self.assertRaisesRegex(ValueError,'bounded'):
                read_release_bundle(data)
            data.write_bytes(b'fixture')
            info = os.stat(data)
            with patch('logbook.operator_entry.os.fstat',return_value=SimpleNamespace(st_mode=info.st_mode,st_size=8)):
                with self.assertRaisesRegex(ValueError,'changed'):
                    read_release_bundle(data)
            link = root/'link'
            link.symlink_to(data)
            with self.assertRaises(OSError):
                read_release_bundle(link)

    def test_installed_operator_can_prepare_from_existing_signed_release_package(self):
        from logbook.operator_entry import prepare_named
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root/'state'
            package = root/'release.tar.gz'
            package.write_bytes(b'signed release fixture')
            options = {'remote_parent':'/var/lib/logbook-graph-guard/bundles', 'journal_dir':'/var/lib/logbook-graph-guard/journals',
                       'container_id':'a'*64, 'pid':42, 'revision':'b'*40, 'release_bundle':str(package),
                       'host':'graph', 'identity':'/key', 'seconds':30, 'protected_executor':True}
            plan = {'nodes':[], 'relationships':[], 'generated_at':'now'}
            with patch('logbook.operator_entry.verify_checkout') as checkout, \
                 patch('logbook.operator_entry.build_release_bundle') as build, \
                 patch('logbook.operator_entry.request_generated_plan', return_value=(plan, {})), \
                 patch('logbook.operator_entry.provision_named_bundle') as provision:
                with self.assertRaisesRegex(ValueError,'full release revision'):
                    prepare_named(None, 7, {**options,'revision':'bad'}, state)
                prepare_named(None, 7, options, state)
                checkout.assert_not_called()
                build.assert_not_called()
                self.assertEqual(provision.call_args.args[-1], b'signed release fixture')

    def test_staged_protected_worker_and_recovery_use_fixed_executor(self):
        from logbook.graph_maintenance_stage import stage_plan
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage = root/'window'
            executor = root/'executor'
            journals = root/'journals'
            for directory in (stage, executor, journals):
                directory.mkdir(mode=0o700)
            request = {'private_dir':str(stage), 'journal':str(journals/'job.json'),
                       'worker':str(stage/'worker'), 'recovery_worker':str(stage/'restore'),
                       'recovery_file':str(executor/'logbook/graph_maintenance_recovery.py'),
                       'container_id':'a'*64, 'pid':42, 'port':7697, 'unit':'unit',
                       'plan_data':{'nodes':[], 'relationships':[], 'generated_at':'now'},
                       'profile':'named-graph', 'plan_proof':{}}
            stage_plan(request, executor)
            for name in ('worker', 'restore'):
                self.assertIn('PYTHONPATH='+str(executor), (stage/name).read_text())
    def test_prepare_named_selects_fixed_recovery_for_protected_execution(self):
        from logbook.operator_entry import prepare_named
        with TemporaryDirectory() as tmp:
            state = Path(tmp)/'state'
            options = {'remote_parent':'/var/lib/logbook-graph-guard/bundles', 'journal_dir':'/var/lib/logbook-graph-guard/journals',
                       'container_id':'a'*64, 'pid':42, 'revision':'b'*40, 'wheel_dir':'/wheels',
                       'host':'graph', 'identity':'/key', 'seconds':30, 'protected_executor':True}
            plan = {'nodes':[], 'relationships':[], 'generated_at':'now'}
            with patch('logbook.operator_entry.verify_checkout'), \
                 patch('logbook.operator_entry.build_release_bundle', return_value=b'bundle'), \
                 patch('logbook.operator_entry.request_generated_plan', return_value=(plan, {})), \
                 patch('logbook.operator_entry.provision_named_bundle'):
                prepare_named(None, 7, options, state)
            request = json.loads(state.read_text())['request']
            self.assertEqual(request['recovery_file'], '/var/lib/logbook-graph-guard/executor/logbook/graph_maintenance_recovery.py')
    def test_protected_execution_verifies_release_but_uses_fixed_code(self):
        from logbook.graph_maintenance_remote import verify_execution_marker
        token = 'a'*32
        request = {'boot_guard_dir':'/guard', 'unit':'logbook-graph-maintenance-'+token+'.service'}
        with patch('logbook.graph_maintenance_remote.__file__', '/guard/executor/logbook/graph_maintenance_remote.py'), \
             patch('logbook.graph_maintenance_remote.verify_bundle_marker') as verify:
            self.assertEqual(verify_execution_marker(request), Path('/guard/executor'))
            verify.assert_called_once_with(request, Path('/guard/executor'), Path('/guard/bundles/bundle-'+token))
            with self.assertRaises(ValueError):
                verify_execution_marker({**request, 'unit':'../invalid'})
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

    def test_confirmed_prestart_outcome_requires_matching_receipt_and_no_gate(self):
        from logbook.graph_maintenance_remote import confirmed_normal_outcome
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            request={'journal':str(root/'journal'),'private_dir':str(root),'profile':'named-graph',
                     'container_id':'a'*64,'pid':123,'port':7697,'unit':'logbook-graph-maintenance-test.service'}
            receipt=root/'journal.outcome'
            receipt.write_text(json.dumps({k:request[k] for k in ('container_id','pid','port','unit')} |
                                          {'batch':None,'outcome':'not-retained'}))
            receipt.chmod(0o600)
            self.assertEqual(confirmed_normal_outcome(request),'not-retained')
            (root/'worker-start').touch()
            with self.assertRaisesRegex(ValueError, 'pre-start'):
                confirmed_normal_outcome(request)
            (root/'worker-start').unlink()
            receipt.write_text(json.dumps({'batch':None,'outcome':'not-retained'}))
            with self.assertRaisesRegex(ValueError, 'unconfirmed pre-start'):
                confirmed_normal_outcome(request)

    def test_claimed_cancellation_requires_matching_target_and_no_fence(self):
        from logbook.graph_maintenance_remote import prepare_claimed_cancellation
        recovery = Mock(pid=123, table='logbook_fixture')
        launcher = Mock()
        request = {'boot_guard_dir':'/var/lib/logbook-graph-guard'}
        with patch('logbook.graph_maintenance_remote.verify_memgraph_binding') as binding, \
             patch('logbook.graph_maintenance_remote.MemgraphBootGuard') as guard:
            recovery.run.return_value = json.dumps({'nftables':[{'metainfo':{}},
                                         {'table':{'family':'inet','name':'logbook_fixture'}}]})
            with self.assertRaisesRegex(RuntimeError, 'fence'):
                prepare_claimed_cancellation(request,recovery,launcher)
            binding.assert_not_called()
            guard.assert_not_called()
            recovery.run.return_value = json.dumps({'nftables':[{'metainfo':{}}]})
            prepare_claimed_cancellation(request,recovery,launcher)
            launcher.verify_target.assert_called()
            binding.assert_called_once_with(recovery)
            guard.return_value.allow.assert_called_once()
            recovery.run.return_value = json.dumps({'nftables':[]})
            with self.assertRaisesRegex(RuntimeError, 'incomplete'):
                prepare_claimed_cancellation(request,recovery,launcher)

    def test_remote_cancel_invokes_claimed_window_safety_check(self):
        request={'container_id':'id','pid':1,'port':7697,'unit':'unit','private_dir':'/private',
                 'worker':'/worker','recovery_file':'/recovery','seconds':10,'profile':'named-graph',
                 'boot_guard_service':'memgraph.service','boot_guard_dir':'/var/lib/logbook-graph-guard'}
        with patch('logbook.graph_maintenance_remote.DockerRecovery') as recovery, \
             patch('logbook.graph_maintenance_remote.DockerLaunch') as launcher, \
             patch('logbook.graph_maintenance_remote.prepare_claimed_cancellation') as prepare, \
             patch('sys.stdout',new_callable=io.StringIO) as out, \
             patch('sys.argv',['remote','named-cancel']), patch('sys.stdin',io.StringIO(json.dumps(request))):
            launcher.return_value.cancel_unstarted.side_effect = lambda check: check() or True
            remote_main()
            prepare.assert_called_once_with(request,recovery.return_value,launcher.return_value)
            self.assertEqual(out.getvalue(),'CANCELLED\n')

    def test_remote_cancel_marks_only_never_started_windows_recovered(self):
        request={'container_id':'id','pid':1,'port':7697,'unit':'unit','private_dir':'/private',
                 'worker':'/worker','recovery_file':'/recovery','seconds':10,'profile':'named-graph',
                 'boot_guard_service':'memgraph.service','boot_guard_dir':'/var/lib/logbook-graph-guard'}
        with patch('logbook.graph_maintenance_remote.DockerRecovery'), \
             patch('logbook.graph_maintenance_remote.DockerLaunch') as launcher, \
             patch('sys.stdout',new_callable=io.StringIO) as out:
            launcher.return_value.cancel_unstarted.side_effect = [True, False]
            launcher.return_value.cancelled.return_value = True
            for action in ('named-cancel','named-cancel','named-status','named-outcome'):
                with patch('sys.argv',['remote',action]), patch('sys.stdin',io.StringIO(json.dumps(request))):
                    remote_main()
            self.assertEqual(out.getvalue(),'CANCELLED\nPENDING\nRECOVERED\nnot-retained\n')

    def test_remote_named_status_and_outcome_require_verified_restart(self):
        request={'container_id':'id','pid':1,'port':7697,'unit':'unit','private_dir':'/private',
                 'worker':'/worker','recovery_file':'/recovery','seconds':10,'profile':'named-graph',
                 'boot_guard_service':'memgraph.service','boot_guard_dir':'/var/lib/logbook-graph-guard'}
        with patch('logbook.graph_maintenance_remote.DockerRecovery'), \
             patch('logbook.graph_maintenance_remote.DockerLaunch'), \
             patch('logbook.graph_maintenance_remote.recovery_confirmed',return_value=False), \
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

    def test_named_cancel_requires_expected_response(self):
        from logbook.graph_maintenance_cross_host import NamedGraphMaintenance
        remote=NamedGraphMaintenance('host',Path('/key'),Path('/entry'),{})
        with patch.object(remote,'invoke',side_effect=['CANCELLED','PENDING','invalid']):
            self.assertTrue(remote.cancel_unstarted())
            self.assertFalse(remote.cancel_unstarted())
            with self.assertRaisesRegex(ConnectionError,'unexpected'):
                remote.cancel_unstarted()
        self.assertIn('named-cancel',remote._command('named-cancel'))

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
             patch('logbook.graph_maintenance_remote.recovery_confirmed',return_value=False), \
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

    def test_named_check_requires_proof_and_positive_result(self):
        from logbook.graph_maintenance_cross_host import NamedGraphMaintenance
        remote=NamedGraphMaintenance('host',Path('/key'),Path('/entry'),{})
        with self.assertRaisesRegex(ValueError, 'proof required'):
            remote.check({'nodes':[]})
        remote.plan_proof = {'proof':'fixture'}
        with patch.object(remote, 'invoke', return_value='CHECK_OK') as invoke:
            remote.check({'nodes':[]})
            self.assertEqual(invoke.call_args.args[0], 'named-check')
        with patch.object(remote, 'invoke', return_value='REJECTED'), \
             self.assertRaisesRegex(ConnectionError, 'check rejected'):
            remote.check({'nodes':[]})

    def test_named_stage_rejects_missing_producer_proof_before_disarming(self):
        request={'container_id':'a'*64,'pid':1,'port':7697,'unit':'unit','private_dir':'/private',
                 'worker':'/worker','recovery_file':'/recovery','seconds':10,'profile':'named-graph',
                 'boot_guard_service':'memgraph.service','boot_guard_dir':'/var/lib/logbook-graph-guard',
                 'job_id':7,'plan_data':{'nodes':[]}}
        with patch('logbook.graph_maintenance_remote.DockerRecovery'), \
             patch('logbook.graph_maintenance_remote.DockerLaunch'), \
             patch('logbook.graph_maintenance_remote.verify_bundle_marker'), \
             patch('logbook.graph_maintenance_remote.authorize_repair'), \
             patch('logbook.graph_maintenance_remote.record_named_graph_storage') as record, \
             patch('logbook.graph_maintenance_remote.MemgraphBootGuard') as guard, \
             patch('logbook.graph_maintenance_remote.stage_plan'), \
             patch('sys.argv',['remote','named-stage']), patch('sys.stdin',io.StringIO(json.dumps(request))), \
             self.assertRaises((KeyError, ValueError)):
            remote_main()
        record.assert_not_called()
        guard.return_value.disarm.assert_not_called()

    def test_named_check_is_read_only_and_checks_schema_before_guard(self):
        request={'container_id':'a'*64,'pid':1,'port':7697,'unit':'unit','private_dir':'/private',
                 'worker':'/worker','recovery_file':'/recovery','seconds':10,'profile':'named-graph',
                 'boot_guard_service':'memgraph.service','boot_guard_dir':'/var/lib/logbook-graph-guard',
                 'job_id':7,'plan_data':{'nodes':[]},'plan_proof':{}}
        with patch('logbook.graph_maintenance_remote.DockerRecovery') as recovery, \
             patch('logbook.graph_maintenance_remote.DockerLaunch'), \
             patch('logbook.graph_maintenance_remote.verify_bundle_marker'), \
             patch('logbook.graph_maintenance_remote.authorize_repair'), \
             patch('logbook.graph_maintenance_remote.verify_plan'), \
             patch('logbook.graph_maintenance_remote.record_named_graph_storage') as record, \
             patch('logbook.graph_maintenance_remote.MemgraphBootGuard') as guard, \
             patch('logbook.graph_maintenance_remote.stage_plan') as stage, \
             patch('sys.argv',['remote','named-check']), patch('sys.stdin',io.StringIO(json.dumps(request))), \
             patch('sys.stdout',new_callable=io.StringIO) as output:
            remote_main()
            self.assertEqual(output.getvalue(),'CHECK_OK\n')
            self.assertIn('logbook.repair_schema_preflight', recovery.return_value.run.call_args.args[0])
            record.assert_not_called()
            guard.return_value.disarm.assert_not_called()
            stage.assert_not_called()
            recovery.return_value.run.side_effect = ValueError('missing constraints')
            with patch('sys.argv',['remote','named-stage']), patch('sys.stdin',io.StringIO(json.dumps(request))), \
                 self.assertRaisesRegex(ValueError, 'missing constraints'):
                remote_main()
            record.assert_not_called()
            guard.return_value.disarm.assert_not_called()
            stage.assert_not_called()

    def test_named_stage_checks_standing_policy_before_disarming_and_staging(self):
        request={'container_id':'a'*64,'pid':1,'port':7697,'unit':'unit','private_dir':'/private',
                 'worker':'/worker','recovery_file':'/recovery','seconds':10,'profile':'named-graph',
                 'boot_guard_service':'memgraph.service','boot_guard_dir':'/var/lib/logbook-graph-guard','job_id':7,
                 'plan_data':{'nodes':[]}}
        events=[]
        with patch('logbook.graph_maintenance_remote.DockerRecovery') as recovery, \
             patch('logbook.graph_maintenance_remote.DockerLaunch') as launcher, \
             patch('logbook.graph_maintenance_remote.verify_bundle_marker'), \
             patch('logbook.graph_maintenance_remote.verify_plan'), \
             patch('logbook.graph_maintenance_remote.authorize_repair',side_effect=lambda *a:events.append('policy')), \
             patch('logbook.graph_maintenance_remote.record_named_graph_storage',side_effect=lambda *a:events.append('record')), \
             patch('logbook.graph_maintenance_remote.MemgraphBootGuard') as guard, \
             patch('logbook.graph_maintenance_remote.stage_plan',side_effect=lambda *a:events.append('stage')), \
             patch('sys.stdout',new_callable=io.StringIO), patch('sys.argv',['remote','named-stage']), \
             patch('sys.stdin',io.StringIO(json.dumps(request))):
            launcher.return_value.verify_target.side_effect=lambda:events.append('target')
            guard.return_value.disarm.side_effect=lambda:events.append('guard')
            remote_main()
            self.assertEqual(events,['target','policy','record','stage'])
            guard.return_value.disarm.assert_not_called()
            recovery.assert_called_once()

    def test_named_preflight_and_launch_recheck_staged_plan_and_policy(self):
        request={'container_id':'a'*64,'pid':1,'port':7697,'unit':'unit','private_dir':'/private',
                 'worker':'/worker','recovery_file':'/recovery','seconds':10,'profile':'named-graph',
                 'boot_guard_service':'memgraph.service','boot_guard_dir':'/var/lib/logbook-graph-guard','job_id':7}
        with patch('logbook.graph_maintenance_remote.DockerRecovery'), \
             patch('logbook.graph_maintenance_remote.DockerLaunch') as launcher, \
             patch('logbook.graph_maintenance_remote.verify_bundle_marker'), \
             patch('logbook.graph_maintenance_remote.verify_plan'), \
             patch('logbook.graph_maintenance_remote.read_private_json',return_value={'nodes':[]}), \
             patch('logbook.graph_maintenance_remote.authorize_repair') as approval, \
             patch('sys.stdout',new_callable=io.StringIO) as out:
            for action in ('named-preflight','named-launch'):
                with patch('sys.argv',['remote',action]), patch('sys.stdin',io.StringIO(json.dumps(request))):
                    remote_main()
            self.assertEqual(approval.call_count,2)
            launcher.return_value.preflight.assert_called_once()
            launcher.return_value.run_window.assert_called_once()
            self.assertEqual(out.getvalue(),'PREFLIGHT_OK\nLAUNCHED\n')

    def test_policy_rejection_blocks_stage_and_launch(self):
        request={'container_id':'a'*64,'pid':1,'port':7697,'unit':'unit','private_dir':'/private',
                 'worker':'/worker','recovery_file':'/recovery','seconds':10,'profile':'named-graph',
                 'boot_guard_service':'memgraph.service','boot_guard_dir':'/var/lib/logbook-graph-guard',
                 'plan_data':{'nodes':[]}}
        with patch('logbook.graph_maintenance_remote.DockerRecovery'), \
             patch('logbook.graph_maintenance_remote.DockerLaunch') as launcher, \
             patch('logbook.graph_maintenance_remote.verify_bundle_marker'), \
             patch('logbook.graph_maintenance_remote.read_private_json',return_value={'nodes':[]}), \
             patch('logbook.graph_maintenance_remote.authorize_repair',side_effect=ValueError('policy rejected')), \
             patch('logbook.graph_maintenance_remote.MemgraphBootGuard') as guard:
            for action in ('named-stage','named-preflight','named-launch'):
                with patch('sys.argv',['remote',action]), patch('sys.stdin',io.StringIO(json.dumps(request))), \
                     self.assertRaisesRegex(ValueError,'policy rejected'):
                    remote_main()
            guard.return_value.disarm.assert_not_called()
            launcher.return_value.preflight.assert_not_called()
            launcher.return_value.run_window.assert_not_called()

    def test_real_signed_policy_is_rechecked_across_stage_and_launch(self):
        import subprocess
        import time
        from logbook.named_graph_approval import plan_digest
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            guard_dir=root/'guard'
            stage=root/'stage'
            guard_dir.mkdir(mode=0o700)
            stage.mkdir(mode=0o700)
            key=root/'owner'
            subprocess.run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True)
            signers=guard_dir/'approval.allowed_signers'
            signers.write_text('owner '+key.with_suffix('.pub').read_text())
            signers.chmod(0o600)
            policy=guard_dir/'standing-policy.json'
            policy.write_text(json.dumps({'schema':1,'revision':'a'*40,'bundle_sha256':'b'*64,
                                          'max_records':5,'expires_at':int(time.time())+300}))
            policy.chmod(0o600)
            subprocess.run(['/usr/bin/ssh-keygen','-Y','sign','-n','logbook-graph-policy',
                            '-f',str(key),str(policy)],check=True,capture_output=True)
            (guard_dir/'standing-policy.json.sig').chmod(0o600)
            plan={'nodes':[{'id':'logbook:job:000147','labels':['LogbookJob'],
                            'properties':{'project':'logbook','job_id':147}}],
                  'relationships':[],'generated_at':'now'}
            from logbook.plan_attestation import issue_plan
            producer=root/'producer'
            subprocess.run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-f',str(producer)],check=True)
            allowed=guard_dir/'plan.allowed_signers'
            allowed.write_text('producer '+producer.with_suffix('.pub').read_text())
            allowed.chmod(0o600)
            def generate(_config, _job, path):
                path.write_text(json.dumps(plan))
                path.chmod(0o600)
            with patch('logbook.plan_attestation.prepare_job_plan',side_effect=generate):
                _, proof = issue_plan(None,147,producer)
            request={'container_id':'c'*64,'pid':1,'port':7697,'unit':'unit','private_dir':str(stage),
                     'worker':'/worker','recovery_file':'/recovery','seconds':10,'profile':'named-graph',
                     'boot_guard_service':'memgraph.service','boot_guard_dir':str(guard_dir),
                     'revision':'a'*40,'bundle_sha256':'b'*64,'job_id':147,
                     'plan_sha256':plan_digest(plan),'plan_data':plan,'plan_proof':proof}
            def fake_stage(_request, _bundle):
                path=stage/'plan.json'
                path.write_text(json.dumps(plan))
                path.chmod(0o600)
                proof_path=stage/'plan-proof.json'
                proof_path.write_text(json.dumps(proof))
                proof_path.chmod(0o600)
            with patch('logbook.graph_maintenance_remote._validate_named'), \
                 patch('logbook.graph_maintenance_remote.DockerRecovery'), \
                 patch('logbook.graph_maintenance_remote.DockerLaunch') as launcher, \
                 patch('logbook.graph_maintenance_remote.verify_bundle_marker'), \
                 patch('logbook.graph_maintenance_remote.record_named_graph_storage'), \
                 patch('logbook.graph_maintenance_remote.MemgraphBootGuard'), \
                 patch('logbook.graph_maintenance_remote.stage_plan',side_effect=fake_stage), \
                 patch('sys.stdout',new_callable=io.StringIO):
                for action in ('named-stage','named-preflight'):
                    with patch('sys.argv',['remote',action]), patch('sys.stdin',io.StringIO(json.dumps(request))):
                        remote_main()
                proof_path=stage/'plan-proof.json'
                changed={**proof,'signature':proof['signature'][:-10]}
                proof_path.write_text(json.dumps(changed))
                with patch('sys.argv',['remote','named-launch']), patch('sys.stdin',io.StringIO(json.dumps(request))), \
                     self.assertRaisesRegex(ValueError,'signature'):
                    remote_main()
                proof_path.write_text(json.dumps(proof))
                policy.write_text('{}')
                with patch('sys.argv',['remote','named-launch']), patch('sys.stdin',io.StringIO(json.dumps(request))), \
                     self.assertRaisesRegex(ValueError,'signature'):
                    remote_main()
                launcher.return_value.run_window.assert_not_called()

    def test_named_staging_failure_keeps_processing_blocked(self):
        from logbook.graph_maintenance_cross_host import coordinate_leased_window
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            config=SimpleNamespace(processing_root=root)
            remote=Mock(request={'unit':'logbook-graph-maintenance-'+'a'*32+'.service'})
            remote.recovered.return_value=False
            remote.stage.side_effect=ValueError('approval rejected')
            def under_locks(_config, callback):
                callback(3,4)
            with patch('logbook.graph_maintenance_cross_host.with_logbook_lock_descriptors',side_effect=under_locks), \
                 self.assertRaisesRegex(ValueError,'approval rejected'):
                coordinate_leased_window(config,remote,timeout_seconds=1,stage_plan={'nodes':[]})
            self.assertTrue((root/'graph-maintenance.pending').exists())
            remote.preflight.assert_not_called()
            remote.launch.assert_not_called()

    def test_named_normal_outcome_allows_restart_only_after_verified_result(self):
        request={'container_id':'id','pid':1,'port':7697,'unit':'unit','private_dir':'/private',
                 'worker':'/worker','recovery_file':'/recovery','seconds':10,'profile':'named-graph',
                 'boot_guard_service':'memgraph.service','boot_guard_dir':'/var/lib/logbook-graph-guard'}
        with patch('logbook.graph_maintenance_remote.DockerRecovery'), \
             patch('logbook.graph_maintenance_remote.DockerLaunch'), \
             patch('logbook.graph_maintenance_remote.recovery_confirmed',return_value=True), \
             patch('logbook.graph_maintenance_remote.confirmed_normal_outcome',return_value='retained') as result, \
             patch('logbook.graph_maintenance_remote.verify_memgraph_binding') as binding, \
             patch('logbook.graph_maintenance_remote.MemgraphBootGuard') as guard, \
             patch('sys.stdout',new_callable=io.StringIO) as out, \
             patch('sys.argv',['remote','named-outcome']), patch('sys.stdin',io.StringIO(json.dumps(request))):
            remote_main()
            result.assert_called_once()
            binding.assert_called_once()
            guard.return_value.allow.assert_called_once()
            self.assertEqual(out.getvalue(),'retained\n')

    def test_prepare_and_run_bind_private_plan_to_named_target(self):
        from logbook.operator_entry import prepare_named, run_named
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            root.chmod(0o700)
            state=root/'state.json'
            options={'remote_parent':'/var/lib/logbook-graph-guard/bundles', 'journal_dir':'/var/lib/logbook-graph-guard/journals',
                     'container_id':'a'*64, 'pid':42, 'revision':'b'*40, 'wheel_dir':'/wheels',
                     'host':'graph', 'identity':'/key', 'seconds':30}
            plan={'nodes':[{'id':'one'}], 'relationships':[], 'generated_at':'first'}
            with patch('logbook.operator_entry.verify_checkout'), \
                 patch('logbook.operator_entry.build_release_bundle',return_value=b'bundle'), \
                 patch('logbook.operator_entry.request_generated_plan',return_value=(plan,{'fixture':'proof'})), \
                 patch('logbook.operator_entry.provision_named_bundle') as provision:
                self.assertEqual(prepare_named(Mock(),7,options,state),'prepared')
                provision.assert_called_once()
            saved=json.loads(state.read_text())
            self.assertEqual(saved['request']['job_id'],7)
            self.assertEqual(saved['plan_proof'], {'fixture':'proof'})
            self.assertEqual(saved['request']['boot_guard_service'],'memgraph.service')
            with patch('logbook.operator_entry.coordinate_leased_window') as coordinate, \
                 patch('logbook.operator_entry.request_generated_plan',return_value=({**plan, 'generated_at':'later'}, {'fresh':True})), \
                 patch('logbook.operator_entry.NamedGraphMaintenance') as remote:
                remote.return_value.request=saved['request']
                remote.return_value.outcome.return_value='retained'
                self.assertEqual(run_named(Mock(),state),'retained')
                self.assertEqual(coordinate.call_args.kwargs['stage_plan'](),{**plan, 'generated_at':'later'})
                with patch('logbook.lock_guard_client.coordinate_named_handoff') as handoff:
                    self.assertEqual(run_named(None, state, restricted=True), 'retained')
                    handoff.assert_called_once()
                with self.assertRaisesRegex(ValueError, 'processing configuration required'):
                    run_named(None, state)
                saved['plan']['nodes'].append({'id':'changed'})
                state.write_text(json.dumps(saved))
                state.chmod(0o600)
                with self.assertRaisesRegex(ValueError,'plan mismatch'):
                    run_named(Mock(),state)

    def test_named_run_refreshes_producer_proof_and_persists_recovery_state(self):
        from logbook.named_graph_approval import plan_digest
        from logbook.operator_entry import run_named
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.chmod(0o700)
            plan = {'nodes':[{'id':'logbook:job:000007'}], 'relationships':[], 'generated_at':'old'}
            fresh = {**plan, 'generated_at':'new'}
            request = {'profile':'named-graph','boot_guard_service':'memgraph.service',
                       'boot_guard_dir':'/var/lib/logbook-graph-guard','port':7697,'seconds':10,
                       'unit':'logbook-graph-maintenance-'+'a'*32+'.service','job_id':7,
                       'plan_sha256':plan_digest(plan)}
            state=root/'state.json'
            state.write_text(json.dumps({'host':'graph','identity':'/key','entrypoint':'/entry',
                                         'request':request,'plan':plan,'plan_proof':{'old':True}}))
            state.chmod(0o600)
            def coordinate(_config, remote, *, timeout_seconds, stage_plan):
                chosen = stage_plan()
                self.assertEqual(chosen, fresh)
                self.assertEqual(remote.plan_proof, {'new':True})
                self.assertEqual(remote.request['plan_sha256'], plan_digest(fresh))
                saved=json.loads(state.read_text())
                self.assertEqual(saved['plan'], fresh)
                self.assertEqual(saved['plan_proof'], {'new':True})
                self.assertEqual(saved['request']['plan_sha256'], plan_digest(fresh))
            with patch('logbook.operator_entry.NamedGraphMaintenance') as remote, \
                 patch('logbook.operator_entry.request_generated_plan',return_value=(fresh,{'new':True})), \
                 patch('logbook.operator_entry.coordinate_leased_window',side_effect=coordinate):
                remote.return_value.request=request
                remote.return_value.outcome.return_value='retained'
                self.assertEqual(run_named(Mock(),state),'retained')

    def test_refresh_failure_preserves_existing_recovery_state(self):
        from logbook.named_graph_approval import plan_digest
        from logbook.operator_entry import run_named
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            root.chmod(0o700)
            plan={'nodes':[], 'relationships':[], 'generated_at':'old'}
            fresh={**plan,'generated_at':'new'}
            request={'profile':'named-graph','boot_guard_service':'memgraph.service',
                     'boot_guard_dir':'/var/lib/logbook-graph-guard','port':7697,'seconds':10,
                     'unit':'logbook-graph-maintenance-'+'a'*32+'.service','job_id':7,
                     'plan_sha256':plan_digest(plan)}
            state=root/'state.json'
            state.write_text(json.dumps({'host':'graph','identity':'/key','entrypoint':'/entry',
                                         'request':request,'plan':plan,'plan_proof':{'old':True}}))
            state.chmod(0o600)
            previous=state.read_bytes()
            def coordinate(_config, _remote, *, timeout_seconds, stage_plan):
                stage_plan()
            with patch('logbook.operator_entry.NamedGraphMaintenance') as remote, \
                 patch('logbook.operator_entry.request_generated_plan',return_value=(fresh,{'new':True})), \
                 patch('logbook.operator_entry.coordinate_leased_window',side_effect=coordinate), \
                 patch('logbook.operator_entry.os.replace',side_effect=OSError('simulated failure')), \
                 self.assertRaisesRegex(OSError,'simulated failure'):
                remote.return_value.request=request
                run_named(Mock(),state)
            self.assertEqual(state.read_bytes(),previous)
            self.assertEqual(list(root.glob('.repair-state-*')),[])
            self.assertFalse((root/'graph-maintenance.pending').exists())
            with patch('logbook.operator_entry.NamedGraphMaintenance') as remote, \
                 patch('logbook.operator_entry.request_generated_plan',return_value=(fresh,{'data':'x'*5000001})), \
                 patch('logbook.operator_entry.coordinate_leased_window',side_effect=coordinate), \
                 self.assertRaisesRegex(ValueError,'state exceeds'):
                remote.return_value.request=request
                run_named(Mock(),state)
            self.assertEqual(state.read_bytes(),previous)

    def test_named_run_rechecks_ledger_under_locks_before_staging(self):
        from logbook.named_graph_approval import plan_digest
        from logbook.operator_entry import run_named
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.chmod(0o700)
            plan = {'nodes': [{'id': 'logbook:job:000007', 'properties': {'text': 'original'}}],
                    'relationships': [], 'generated_at': 'first'}
            request = {'profile': 'named-graph', 'boot_guard_service': 'memgraph.service',
                       'boot_guard_dir': '/var/lib/logbook-graph-guard', 'port': 7697,
                       'unit': 'logbook-graph-maintenance-' + 'a'*32 + '.service',
                       'job_id': 7, 'plan_sha256': plan_digest(plan), 'seconds': 10}
            state = root/'state.json'
            state.write_text(json.dumps({'host': 'graph', 'identity': '/key', 'entrypoint': '/entry',
                                         'request': request, 'plan': plan}))
            state.chmod(0o600)
            config = SimpleNamespace(processing_root=root)
            lock_held = False
            def locks(_config, callback):
                nonlocal lock_held
                lock_held = True
                try:
                    callback(3, 4)
                finally:
                    lock_held = False
            def prepare(job):
                self.assertTrue(lock_held)
                self.assertEqual(job, 7)
                changed = {**plan, 'nodes': [{'id': 'logbook:job:000007',
                                               'properties': {'text': 'changed'}}]}
                return changed, {}
            with patch('logbook.operator_entry.NamedGraphMaintenance') as remote, \
                 patch('logbook.operator_entry.request_generated_plan', side_effect=prepare), \
                 patch('logbook.graph_maintenance_cross_host.with_logbook_lock_descriptors', side_effect=locks), \
                 self.assertRaisesRegex(ValueError, 'ledger'):
                remote.return_value.request = request
                remote.return_value.recovered.return_value = False
                remote.return_value.stage.side_effect = AssertionError('ledger recheck skipped')
                run_named(config, state)
            remote.return_value.stage.assert_not_called()
            remote.return_value.launch.assert_not_called()
            self.assertFalse((root/'graph-maintenance.pending').exists())

    def test_named_remote_checks_each_launch_response(self):
        from logbook.graph_maintenance_cross_host import NamedGraphMaintenance
        from logbook.graph_maintenance_launcher import StartupRejected
        remote=NamedGraphMaintenance('host',Path('/key'),Path('/entry'),{})
        with self.assertRaisesRegex(ValueError, 'proof'):
            remote.stage({})
        remote.plan_proof = {'fixture': True}
        with patch.object(remote,'invoke',side_effect=['STAGED','bad','PREFLIGHT_OK','bad','LAUNCHED','REJECTED','bad']):
            remote.stage({})
            with self.assertRaises(ConnectionError):
                remote.stage({})
            remote.preflight()
            with self.assertRaises(ConnectionError):
                remote.preflight()
            remote.launch()
            with self.assertRaises(StartupRejected):
                remote.launch()
            with self.assertRaises(ConnectionError):
                remote.launch()

    def test_named_preflight_failure_preserves_processing_guard(self):
        from logbook.graph_maintenance_cross_host import coordinate_leased_window
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            remote=Mock(request={'unit':'logbook-graph-maintenance-'+'a'*32+'.service'})
            remote.recovered.return_value=False
            remote.preflight.side_effect=ValueError('expired')
            with patch('logbook.graph_maintenance_cross_host.with_logbook_lock_descriptors',
                       side_effect=lambda config, callback: callback(3,4)), self.assertRaises(ValueError):
                coordinate_leased_window(SimpleNamespace(processing_root=root),remote,timeout_seconds=1,stage_plan={})
            self.assertTrue((root/'graph-maintenance.pending').exists())

    def test_named_rejected_launch_and_normal_status(self):
        from logbook.graph_maintenance_launcher import StartupRejected
        request={'container_id':'id','pid':1,'port':7697,'unit':'unit','private_dir':'/private',
                 'worker':'/worker','recovery_file':'/recovery','seconds':10,'profile':'named-graph',
                 'boot_guard_service':'memgraph.service','boot_guard_dir':'/var/lib/logbook-graph-guard','job_id':7}
        with patch('logbook.graph_maintenance_remote.DockerRecovery'), \
             patch('logbook.graph_maintenance_remote.DockerLaunch') as launcher, \
             patch('logbook.graph_maintenance_remote.verify_bundle_marker'), \
             patch('logbook.graph_maintenance_remote.verify_plan'), \
             patch('logbook.graph_maintenance_remote.read_private_json',return_value={'nodes':[]}), \
             patch('logbook.graph_maintenance_remote.authorize_repair'), \
             patch('logbook.graph_maintenance_remote.recovery_confirmed',side_effect=[True,ValueError()]), \
             patch('logbook.graph_maintenance_remote.fixture_restarted',return_value=False), \
             patch('sys.stdout',new_callable=io.StringIO) as out:
            launcher.return_value.run_window.side_effect=StartupRejected('no')
            for action in ('named-launch','named-status','named-status'):
                with patch('sys.argv',['remote',action]), patch('sys.stdin',io.StringIO(json.dumps(request))):
                    remote_main()
            self.assertEqual(out.getvalue(),'REJECTED\nRECOVERED\nPENDING\n')

    def test_prepares_reviewable_unsigned_owner_approval(self):
        from logbook.operator_entry import prepare_owner_approval
        from logbook.named_graph_approval import FIELDS, plan_digest
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            state=root/'state.json'
            plan={'nodes':[]}
            request={'profile':'named-graph','boot_guard_service':'memgraph.service',
                     'boot_guard_dir':'/var/lib/logbook-graph-guard','port':7697,
                     'container_id':'a'*64,'pid':12,'unit':'logbook-graph-maintenance-'+'c'*32+'.service',
                     'job_id':1,'revision':'b'*40,'bundle_sha256':'d'*64,'plan_sha256':plan_digest(plan)}
            state.write_text(json.dumps({'host':'graph','identity':'/key','entrypoint':'/entry','request':request,'plan':plan}))
            state.chmod(0o600)
            approval=root/'approval.json'
            with patch('logbook.operator_entry.time.time',return_value=1000):
                self.assertEqual(prepare_owner_approval(state,approval),'approval-prepared')
            result=json.loads(approval.read_text())
            self.assertEqual(set(result),{'schema','approved_at','expires_at',*FIELDS})
            self.assertEqual(result['approved_at'],1000)
            self.assertEqual(result['expires_at'],1300)
            self.assertFalse((root/'approval.json.sig').exists())
            with self.assertRaises(FileExistsError):
                prepare_owner_approval(state,approval)
            saved=json.loads(state.read_text())
            saved['plan']={'nodes':['changed']}
            state.write_text(json.dumps(saved))
            state.chmod(0o600)
            with self.assertRaisesRegex(ValueError,'plan mismatch'):
                prepare_owner_approval(state,root/'other.json')
