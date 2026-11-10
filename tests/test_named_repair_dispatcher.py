from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from logbook.named_repair_dispatcher import validate_request


class NamedRepairDispatcherTests(TestCase):
    def test_plan_discovery_dispatches_only_fixed_executor(self):
        import io
        import json
        import sys
        from types import SimpleNamespace
        from logbook.named_repair_dispatcher import main
        executor=Path('/var/lib/logbook-graph-guard/executor/logbook/graph_maintenance_remote.py')
        for request in ({'job_id':1,'plan':{},'proof':{}},{'job_id':1,'plan':{},'proof':{},'extra':1}):
            stdin=io.TextIOWrapper(io.BytesIO(json.dumps(request).encode()),encoding='utf-8')
            with patch('logbook.named_repair_dispatcher.os.geteuid',return_value=0), \
                 patch('logbook.named_repair_dispatcher.protected_executor',return_value=executor), \
                 patch('logbook.named_repair_dispatcher.trusted_path'), \
                 patch('logbook.named_repair_dispatcher.subprocess.run',return_value=SimpleNamespace(returncode=0)) as run, \
                 patch.object(sys,'stdin',stdin), patch.object(sys,'argv',['dispatcher','named-plan-discover']):
                if 'extra' in request:
                    with self.assertRaises(ValueError):
                        main()
                    run.assert_not_called()
                else:
                    with self.assertRaises(SystemExit):
                        main()
                    self.assertEqual(run.call_args.args[0][2],str(executor.parent/'partial_graph_discovery.py'))

    def test_discovery_dispatch_is_fixed_read_only_entry_and_bounded(self):
        import io
        import json
        import sys
        from types import SimpleNamespace
        from logbook.named_repair_dispatcher import main
        executor = Path('/var/lib/logbook-graph-guard/executor/logbook/graph_maintenance_remote.py')
        for request in ({'job_ids':[1,2]}, {'job_ids':[1],'extra':True}, {'job_ids':list(range(1,102))}):
            stdin = io.TextIOWrapper(io.BytesIO(json.dumps(request).encode()), encoding='utf-8')
            with patch('logbook.named_repair_dispatcher.os.geteuid',return_value=0), \
                 patch('logbook.named_repair_dispatcher.protected_executor',return_value=executor), \
                 patch('logbook.named_repair_dispatcher.trusted_path'), \
                 patch('logbook.named_repair_dispatcher.subprocess.run',return_value=SimpleNamespace(returncode=0)) as run, \
                 patch.object(sys,'stdin',stdin), patch.object(sys,'argv',['dispatcher','named-discover']):
                if set(request)=={'job_ids'} and len(request['job_ids'])<=100:
                    with self.assertRaises(SystemExit):
                        main()
                    self.assertEqual(run.call_args.args[0][2],str(executor.parent/'graph_repair_discovery.py'))
                else:
                    with self.assertRaises(ValueError):
                        main()
                    run.assert_not_called()

    def test_dispatch_executes_fixed_fixture_not_release_fixture(self):
        import io
        import json
        import os
        import sys
        from tempfile import TemporaryDirectory
        from logbook.named_repair_dispatcher import main
        with TemporaryDirectory() as tmp:
            guard = Path(tmp)
            token = 'a'*32
            bundle = guard/'bundles'/('bundle-'+token)
            stage = guard/'bundles'/('window-'+token)
            package = guard/'executor'/'logbook'
            for path in (bundle/'logbook', stage, package, guard/'journals'):
                path.mkdir(parents=True, mode=0o700)
            (guard/'standing-policy.json').write_text('{"schema":2}')
            (bundle/'.bundle-ready').write_text('b'*64+'\n')
            good, bad = guard/'good', guard/'bad'
            (bundle/'logbook'/'graph_maintenance_remote.py').write_text(f'from pathlib import Path\nPath({str(bad)!r}).touch()\n')
            for name in ('graph_maintenance_remote.py','graph_maintenance_recovery.py','graph_maintenance_worker.py'):
                path = package/name
                path.write_text(f'from pathlib import Path\nPath({str(good)!r}).touch()\n')
                path.chmod(0o600)
            request = self.request()
            request = {key: value.replace('/var/lib/logbook-graph-guard', str(guard)) if isinstance(value, str) else value
                       for key, value in request.items() if key not in ('plan_data', 'plan_proof')}
            request['recovery_file'] = str(package/'graph_maintenance_recovery.py')
            stdin = io.TextIOWrapper(io.BytesIO(json.dumps(request).encode()), encoding='utf-8')
            real_uid = os.geteuid()
            with patch('logbook.named_repair_dispatcher.GUARD', guard), \
                 patch('logbook.named_repair_dispatcher.os.geteuid', side_effect=[0]+[real_uid]*50), \
                 patch('logbook.named_repair_dispatcher.trusted_path'), \
                 patch.object(sys, 'stdin', stdin), patch.object(sys, 'argv', ['dispatcher', 'named-status']):
                with self.assertRaises(SystemExit) as result:
                    main()
            self.assertEqual(result.exception.code, 0)
            self.assertTrue(good.exists())
            self.assertFalse(bad.exists())

    def test_dispatch_selects_protected_executor_for_version_two_policy(self):
        import io
        import json
        import sys
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace
        from logbook.named_repair_dispatcher import main
        with TemporaryDirectory() as tmp:
            guard = Path(tmp)
            (guard/'standing-policy.json').write_text(json.dumps({'schema':2}))
            request = self.request()
            request['recovery_file'] = str(guard/'executor/logbook/graph_maintenance_recovery.py')
            raw = json.dumps(request).encode()
            stdin = io.TextIOWrapper(io.BytesIO(raw), encoding='utf-8')
            entry = guard/'executor/logbook/graph_maintenance_remote.py'
            with patch('logbook.named_repair_dispatcher.GUARD', guard), \
                 patch('logbook.named_repair_dispatcher.os.geteuid', return_value=0), \
                 patch('logbook.named_repair_dispatcher.trusted_path'), \
                 patch('logbook.named_repair_dispatcher.validate_request', return_value=entry) as validate, \
                 patch('logbook.named_repair_dispatcher.subprocess.run', return_value=SimpleNamespace(returncode=0)) as run, \
                 patch.object(sys, 'stdin', stdin), patch.object(sys, 'argv', ['dispatcher', 'named-status']):
                with self.assertRaises(SystemExit) as result:
                    main()
                self.assertEqual(result.exception.code, 0)
                validate.assert_called_once_with('named-status', request, protected=True)
                self.assertEqual(run.call_args.args[0][2], str(entry))

    def test_protected_executor_is_fixed_and_rejects_untrusted_code(self):
        import os
        from tempfile import TemporaryDirectory
        from logbook.named_repair_dispatcher import protected_executor
        with TemporaryDirectory() as tmp, patch('logbook.named_repair_dispatcher.GUARD', Path(tmp)):
            guard = Path(tmp)
            executor = guard/'executor'
            package = executor/'logbook'
            package.mkdir(parents=True, mode=0o700)
            executor.chmod(0o700)
            for name in ('graph_maintenance_remote.py', 'graph_maintenance_recovery.py', 'graph_maintenance_worker.py'):
                path = package/name
                path.write_text('# protected fixture\n')
                path.chmod(0o600)
            with patch('logbook.named_repair_dispatcher.trusted_path'):
                self.assertEqual(protected_executor(), package/'graph_maintenance_remote.py')
                cache = package/'__pycache__'
                cache.mkdir(mode=0o755)
                (cache/'module.pyc').write_bytes(b'fixture')
                (cache/'module.pyc').chmod(0o644)
                self.assertEqual(protected_executor(), package/'graph_maintenance_remote.py')
                outside = guard/'outside'
                outside.write_text('untrusted')
                (package/'link').symlink_to(outside)
                with self.assertRaisesRegex(ValueError, 'executor'):
                    protected_executor()
                (package/'link').unlink()
                (package/'graph_maintenance_worker.py').chmod(0o666)
                with self.assertRaisesRegex(ValueError, 'executor'):
                    protected_executor()
                with patch('logbook.named_repair_dispatcher.os.walk', side_effect=lambda *a, **k: k['onerror'](OSError('walk failed'))):
                    with self.assertRaisesRegex(OSError, 'walk failed'):
                        protected_executor()
            self.assertEqual(os.geteuid(), package.stat().st_uid)

    def test_protected_request_uses_fixed_executor_not_release_code(self):
        request = self.request()
        guard = Path('/var/lib/logbook-graph-guard')
        executor = guard/'executor'/'logbook'
        request['recovery_file'] = str(executor/'graph_maintenance_recovery.py')
        with patch('logbook.named_repair_dispatcher.trusted_path'), \
             patch('logbook.named_repair_dispatcher.protected_executor', return_value=executor/'graph_maintenance_remote.py'), \
             patch.object(Path, 'read_text', return_value='b'*64+'\n'):
            self.assertEqual(validate_request('named-stage', request, protected=True), executor/'graph_maintenance_remote.py')
            with self.assertRaises(ValueError):
                validate_request('named-stage', self.request(), protected=True)

    def test_protected_executor_runs_instead_of_untrusted_release_fixture(self):
        import subprocess
        import sys
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            guard = Path(tmp)
            token = 'a'*32
            bundle = guard/'bundles'/('bundle-'+token)
            stage = guard/'bundles'/('window-'+token)
            package = guard/'executor'/'logbook'
            for path in (bundle/'logbook', stage, package, guard/'journals'):
                path.mkdir(parents=True, mode=0o700)
            good = guard/'good'
            bad = guard/'bad'
            (bundle/'logbook'/'graph_maintenance_remote.py').write_text(f'from pathlib import Path\nPath({str(bad)!r}).touch()\n')
            (bundle/'.bundle-ready').write_text('b'*64+'\n')
            for name in ('graph_maintenance_remote.py', 'graph_maintenance_recovery.py', 'graph_maintenance_worker.py'):
                target = package/name
                target.write_text(f'from pathlib import Path\nPath({str(good)!r}).touch()\n')
                target.chmod(0o600)
            request = self.request()
            old = '/var/lib/logbook-graph-guard'
            request = {key: value.replace(old, str(guard)) if isinstance(value, str) else value
                       for key, value in request.items()}
            request['recovery_file'] = str(package/'graph_maintenance_recovery.py')
            with patch('logbook.named_repair_dispatcher.GUARD', guard), patch('logbook.named_repair_dispatcher.trusted_path'):
                entry = validate_request('named-stage', request, protected=True)
            subprocess.run([sys.executable, '-I', str(entry)], check=True)
            self.assertTrue(good.exists())
            self.assertFalse(bad.exists())

    def test_dispatch_policy_guard_preserves_original_recovery_and_rejects_bad_policy(self):
        from tempfile import TemporaryDirectory
        from logbook.named_repair_dispatcher import reject_uncontained_policy
        with TemporaryDirectory() as tmp, patch('logbook.named_repair_dispatcher.GUARD', Path(tmp)):
            policy = Path(tmp)/'standing-policy.json'
            reject_uncontained_policy()
            policy.write_text('{"schema": 1}')
            policy.chmod(0o600)
            with patch('logbook.named_repair_dispatcher.trusted_path'):
                reject_uncontained_policy()
                policy.write_text('{}')
                with self.assertRaisesRegex(ValueError, 'disabled'):
                    reject_uncontained_policy()
                policy.write_text(' '*100001)
                with self.assertRaisesRegex(ValueError, 'too large'):
                    reject_uncontained_policy()

    def test_preinstalled_uncontained_release_never_reaches_dispatch(self):
        import io
        import json
        import sys
        from tempfile import TemporaryDirectory
        from logbook.named_repair_dispatcher import main
        with TemporaryDirectory() as tmp:
            guard = Path(tmp)
            (guard/'standing-policy.json').write_text(json.dumps({'schema':2}))
            request = self.request()
            raw = json.dumps(request).encode()
            stdin = io.TextIOWrapper(io.BytesIO(raw), encoding='utf-8')
            with patch('logbook.named_repair_dispatcher.GUARD', guard), \
                 patch('logbook.named_repair_dispatcher.os.geteuid', return_value=0), \
                 patch('logbook.named_repair_dispatcher.trusted_path'), \
                 patch('logbook.named_repair_dispatcher.validate_request'), \
                 patch('logbook.named_repair_dispatcher.subprocess.run') as run, \
                 patch.object(sys, 'stdin', stdin), patch.object(sys, 'argv', ['dispatcher', 'named-status']):
                with self.assertRaisesRegex(ValueError, 'separate release authority is disabled'):
                    main()
                run.assert_not_called()

    def request(self):
        token='a'*32
        guard=Path('/var/lib/logbook-graph-guard')
        bundle=guard/'bundles'/('bundle-'+token)
        stage=guard/'bundles'/('window-'+token)
        return {'profile':'named-graph','boot_guard_service':'memgraph.service','boot_guard_dir':str(guard),
                'port':7697,'unit':'logbook-graph-maintenance-'+token+'.service',
                'private_dir':str(stage),'journal':str(guard/'journals'/(token+'.json')),
                'worker':str(stage/'worker'),'recovery_worker':str(stage/'restore'),
                'recovery_file':str(bundle/'logbook/graph_maintenance_recovery.py'),
                'container_id':'c'*64,'pid':42,'seconds':30,'job_id':147,'revision':'a'*40,
                'bundle_sha256':'b'*64,'plan_sha256':'d'*64,'plan_data':{'nodes':[]},'plan_proof':{}}

    def test_rejects_unapproved_action_and_paths(self):
        request=self.request()
        with patch('logbook.named_repair_dispatcher.trusted_path'), patch.object(Path,'read_text',return_value='b'*64+'\n'):
            validate_request('named-stage',request)
            for action in ('launch','named-unknown','../named-stage'):
                with self.assertRaises(ValueError):
                    validate_request(action,request)
            for field in ('private_dir','journal','worker','recovery_worker','recovery_file'):
                with self.assertRaises(ValueError):
                    validate_request('named-stage',{**request,field:'/tmp/escape'})
            with self.assertRaises(ValueError):
                validate_request('named-stage',{**request,'unit':'other.service'})

    def test_named_client_uses_fixed_dispatcher(self):
        from logbook.graph_maintenance_cross_host import NamedGraphMaintenance
        from types import SimpleNamespace
        remote=NamedGraphMaintenance('repair@odin',Path('/tmp/key'),Path('/any/entry'),{})
        with patch('logbook.graph_maintenance_cross_host.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='RECOVERED\n')) as call:
            self.assertTrue(remote.recovered())
        command=call.call_args.args[0][-1]
        self.assertIn('/usr/local/libexec/logbook-repair-dispatcher',command)
        self.assertNotIn('/usr/bin/python3',command)
        self.assertNotIn('/any/entry',command)

    def test_stage_rejects_extra_request_fields(self):
        request=self.request()
        with patch('logbook.named_repair_dispatcher.trusted_path'):
            with self.assertRaises(ValueError):
                validate_request('named-stage',{**request,'unapproved':'value'})

    def test_rejects_untrusted_ancestors_and_bundle_marker(self):
        import os
        import stat
        from types import SimpleNamespace
        from logbook.named_repair_dispatcher import trusted_path
        good=SimpleNamespace(st_mode=stat.S_IFDIR|0o700,st_uid=os.geteuid())
        bad=SimpleNamespace(st_mode=stat.S_IFLNK|0o777,st_uid=os.geteuid())
        with patch.object(Path,'lstat',return_value=bad), self.assertRaisesRegex(ValueError,'untrusted'):
            trusted_path(Path('/safe/path'),directory=True)
        with patch.object(Path,'lstat',return_value=good), self.assertRaisesRegex(ValueError,'type'):
            trusted_path(Path('/safe/path'),directory=False)
        with patch('logbook.named_repair_dispatcher.trusted_path'), patch.object(Path,'read_text',return_value='wrong\n'):
            with self.assertRaisesRegex(ValueError,'marker'):
                validate_request('named-stage',self.request())

    def test_named_client_rejects_non_named_action(self):
        from logbook.graph_maintenance_cross_host import NamedGraphMaintenance
        remote=NamedGraphMaintenance('repair@odin',Path('/tmp/key'),Path('/entry'),{})
        with self.assertRaises(ValueError):
            remote.invoke('launch')

    def test_named_preparation_rejects_unapproved_directories(self):
        from tempfile import TemporaryDirectory
        from logbook.operator_entry import prepare_named
        with TemporaryDirectory() as tmp:
            parent=Path(tmp)
            parent.chmod(0o700)
            with self.assertRaisesRegex(ValueError,'directories'):
                prepare_named(None,1,{'remote_parent':'/tmp/unsafe','journal_dir':'/tmp/unsafe'},parent/'state')
