from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from logbook.named_repair_dispatcher import validate_request


class NamedRepairDispatcherTests(TestCase):
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
