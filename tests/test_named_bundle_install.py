import hashlib
import io
import json
import subprocess
import tarfile
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from logbook.operator_bundle_bootstrap import install_named_bundle


class NamedBundleInstallerTests(TestCase):
    def test_separately_signed_releases_share_one_policy(self):
        from logbook.standing_graph_policy import authorize_repair
        from logbook.named_graph_approval import plan_digest
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            guard = root/'guard'
            guard.mkdir(mode=0o700)
            parent = guard/'bundles'
            parent.mkdir(mode=0o700)
            keys = {}
            for principal in ('owner', 'release'):
                key = root/principal
                subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
                allowed = guard/('approval.allowed_signers' if principal == 'owner' else 'release.allowed_signers')
                allowed.write_text(principal+' '+key.with_suffix('.pub').read_text())
                allowed.chmod(0o600)
                keys[principal] = key
            def sign(path, principal, namespace):
                sig = Path(str(path)+'.sig')
                sig.unlink(missing_ok=True)
                subprocess.run(['/usr/bin/ssh-keygen', '-Y', 'sign', '-n', namespace, '-f', str(keys[principal]), str(path)], check=True, capture_output=True)
                sig.chmod(0o600)
            policy = guard/'standing-policy.json'
            policy.write_text(json.dumps({'schema':2, 'max_records':5, 'expires_at':int(time.time())+300}))
            policy.chmod(0o600)
            sign(policy, 'owner', 'logbook-graph-policy')
            plan = {'nodes':[{'id':'logbook:job:000147','labels':['LogbookJob'],'properties':{'project':'logbook','job_id':147}}], 'relationships':[], 'generated_at':'now'}
            release = guard/'release.json'
            for version in ('a', 'b'):
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode='w') as tar:
                    data = version.encode()
                    item = tarfile.TarInfo('logbook/example.py')
                    item.size = len(data)
                    tar.addfile(item, io.BytesIO(data))
                payload = stream.getvalue()
                digest = hashlib.sha256(payload).hexdigest()
                release.write_text(json.dumps({'schema':1, 'revision':version*40, 'bundle_sha256':digest}))
                release.chmod(0o600)
                sign(release, 'release', 'logbook-repair-release')
                request = {'revision':version*40, 'bundle_sha256':digest, 'job_id':147, 'plan_sha256':plan_digest(plan)}
                token = version*32
                with patch('logbook.operator_bundle_bootstrap.trusted_parent'):
                    with self.assertRaises(ValueError):
                        install_named_bundle(payload+b'changed', parent/('bundle-'+token), parent/('window-'+token), guard)
                    with self.assertRaisesRegex(ValueError, 'separate release authority is disabled'):
                        install_named_bundle(payload, parent/('bundle-'+token), parent/('window-'+token), guard)
                self.assertFalse((parent/('bundle-'+token)).exists())
                authorize_repair(request, plan, guard)
                with self.assertRaises(ValueError):
                    authorize_repair({**request, 'revision':'c'*40}, plan, guard)
                release.write_text(release.read_text()+' ')
                with self.assertRaises(ValueError):
                    authorize_repair(request, plan, guard)
                sign(release, 'owner', 'logbook-repair-release')
                with self.assertRaises(ValueError):
                    authorize_repair(request, plan, guard)
            for invalid in ('{}', '{'):
                release.write_text(invalid)
                sign(release, 'release', 'logbook-repair-release')
                with self.assertRaisesRegex(ValueError, 'invalid release'):
                    authorize_repair(request, plan, guard)
            from logbook.standing_graph_policy import check_plan
            with self.assertRaisesRegex(ValueError, 'invalid approved release'):
                check_plan(request, plan, json.loads(policy.read_text()))
            policy.write_text(json.dumps({'schema':2, 'max_records':5, 'expires_at':0}))
            sign(policy, 'owner', 'logbook-graph-policy')
            with patch('logbook.operator_bundle_bootstrap.trusted_parent'), self.assertRaisesRegex(ValueError, 'standing policy'):
                install_named_bundle(payload, parent/('bundle-'+'c'*32), parent/('window-'+'c'*32), guard)

    def test_installed_named_command_cannot_use_legacy_installer_mode(self):
        from logbook.operator_bundle_bootstrap import main
        with patch('sys.argv',['/usr/local/libexec/logbook-repair-bundle-installer','/tmp/a','/tmp/b','digest']), \
             patch('logbook.operator_bundle_bootstrap.os.geteuid',return_value=0), \
             patch('logbook.operator_bundle_bootstrap.install_bundle') as legacy:
            with self.assertRaises(SystemExit):
                main()
            legacy.assert_not_called()

    def test_named_client_calls_fixed_installer_not_transmitted_code(self):
        from types import SimpleNamespace
        from logbook.operator_bundle import provision_named_bundle
        token='a'*32
        parent=Path('/var/lib/logbook-graph-guard/bundles')
        with patch('logbook.operator_bundle.subprocess.run',return_value=SimpleNamespace(stdout=b'BUNDLE_INSTALLED\n')) as run:
            provision_named_bundle('repair@odin',Path('/tmp/key'),parent/('bundle-'+token),parent/('window-'+token),b'data')
        command=run.call_args.args[0][-1]
        self.assertIn('/usr/local/libexec/logbook-repair-bundle-installer',command)
        self.assertNotIn('python3 -c',command)
        with self.assertRaises(ValueError):
            provision_named_bundle('repair@odin',Path('/tmp/key'),Path('/bad'),Path('/bad2'),b'data')
        with patch('logbook.operator_bundle.subprocess.run',return_value=SimpleNamespace(stdout=b'wrong')):
            with self.assertRaises(RuntimeError):
                provision_named_bundle('repair@odin',Path('/tmp/key'),parent/('bundle-'+token),parent/('window-'+token),b'data')

    def test_signed_policy_allows_only_approved_bundle_and_fixed_paths(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            guard=root/'guard'
            guard.mkdir(mode=0o700)
            parent=guard/'bundles'
            parent.mkdir(mode=0o700)
            key=root/'owner'
            subprocess.run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True)
            allowed=guard/'approval.allowed_signers'
            allowed.write_text('owner '+key.with_suffix('.pub').read_text())
            allowed.chmod(0o600)
            stream=io.BytesIO()
            with tarfile.open(fileobj=stream,mode='w') as tar:
                item=tarfile.TarInfo('logbook/example.py')
                item.size=2
                tar.addfile(item,io.BytesIO(b'ok'))
            payload=stream.getvalue()
            policy=guard/'standing-policy.json'
            policy.write_text(json.dumps({'schema':1,'revision':'a'*40,'bundle_sha256':hashlib.sha256(payload).hexdigest(),
                                          'max_records':5,'expires_at':int(time.time())+300}))
            policy.chmod(0o600)
            subprocess.run(['/usr/bin/ssh-keygen','-Y','sign','-n','logbook-graph-policy',
                            '-f',str(key),str(policy)],check=True,capture_output=True)
            (guard/'standing-policy.json.sig').chmod(0o600)
            token='a'*32
            with patch('logbook.operator_bundle_bootstrap.trusted_parent'):
                with self.assertRaises(ValueError):
                    install_named_bundle(payload+b'changed',parent/('bundle-'+token),parent/('window-'+token),guard)
                with self.assertRaises(ValueError):
                    install_named_bundle(payload,root/('bundle-'+token),root/('window-'+token),guard)
                guard.chmod(0o755)
                with self.assertRaisesRegex(ValueError,'directory'):
                    install_named_bundle(payload,parent/('bundle-'+token),parent/('window-'+token),guard)
                guard.chmod(0o700)
                policy.chmod(0o644)
                with self.assertRaisesRegex(ValueError,'private policy'):
                    install_named_bundle(payload,parent/('bundle-'+token),parent/('window-'+token),guard)
                policy.chmod(0o600)
                old_policy=policy.read_bytes()
                policy.write_text('{}')
                with self.assertRaisesRegex(ValueError,'signature'):
                    install_named_bundle(payload,parent/('bundle-'+token),parent/('window-'+token),guard)
                policy.write_bytes(old_policy)
                with patch('logbook.operator_bundle_bootstrap.install_bundle',side_effect=OSError('write failed')):
                    with self.assertRaises(OSError):
                        install_named_bundle(payload,parent/('bundle-'+token),parent/('window-'+token),guard)
                self.assertFalse((parent/('window-'+token)).exists())
                install_named_bundle(payload,parent/('bundle-'+token),parent/('window-'+token),guard)
                self.assertEqual((parent/('bundle-'+token)/'logbook/example.py').read_bytes(),b'ok')
                self.assertTrue((parent/('window-'+token)).is_dir())

    def test_signed_malformed_policy_is_rejected(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            guard=root/'guard'
            guard.mkdir(mode=0o700)
            (guard/'bundles').mkdir(mode=0o700)
            key=root/'owner'
            subprocess.run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True)
            allowed=guard/'approval.allowed_signers'
            allowed.write_text('owner '+key.with_suffix('.pub').read_text())
            allowed.chmod(0o600)
            policy=guard/'standing-policy.json'
            policy.write_text('{')
            policy.chmod(0o600)
            subprocess.run(['/usr/bin/ssh-keygen','-Y','sign','-n','logbook-graph-policy',
                            '-f',str(key),str(policy)],check=True,capture_output=True)
            (guard/'standing-policy.json.sig').chmod(0o600)
            token='a'*32
            with patch('logbook.operator_bundle_bootstrap.trusted_parent'), self.assertRaisesRegex(ValueError,'invalid standing policy'):
                install_named_bundle(b'data',guard/'bundles'/('bundle-'+token),guard/'bundles'/('window-'+token),guard)
