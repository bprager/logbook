import subprocess
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch


class RepairReleaseDeliveryTests(TestCase):
    def test_fetch_requires_candidate_parent_and_current_main(self):
        import hashlib
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from logbook.repair_release_delivery import fetch_candidate
        def run(*args, cwd):
            return subprocess.run(args,cwd=cwd,check=True,text=True,capture_output=True).stdout.strip()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            bare = root/'remote.git'
            work = root/'work'
            run('git','init','--bare',str(bare),cwd=root)
            run('git','init','-b','main',str(work),cwd=root)
            (work/'code').write_text('fixture')
            run('git','add','code',cwd=work)
            run('git','-c','user.name=fixture','-c','user.email=fixture@example.invalid','commit','-m','main',cwd=work)
            revision = run('git','rev-parse','HEAD',cwd=work)
            run('git','push',str(bare),'HEAD:refs/heads/main',cwd=work)
            data = b'fixture package'
            release = work/'.logbook-repair'
            release.mkdir()
            (release/'bundle.tar.gz').write_bytes(data)
            (release/'release.json').write_text(json.dumps({'schema':2,'revision':revision,'sequence':1,
                                                             'bundle_sha256':hashlib.sha256(data).hexdigest()}))
            (release/'release.json.sig').write_text('fixture signature')
            run('git','add','.logbook-repair',cwd=work)
            run('git','-c','user.name=fixture','-c','user.email=fixture@example.invalid','commit','-m','candidate',cwd=work)
            run('git','push',str(bare),'HEAD:refs/heads/repair-release-candidates',cwd=work)
            manifest,signature,payload = fetch_candidate(str(bare))
            self.assertEqual(payload,data)
            self.assertEqual(signature,b'fixture signature')
            self.assertEqual(json.loads(manifest)['revision'],revision)
            def amend_candidate():
                run('git','add','.logbook-repair',cwd=work)
                run('git','-c','user.name=fixture','-c','user.email=fixture@example.invalid','commit','--amend','--no-edit',cwd=work)
                run('git','push','--force',str(bare),'HEAD:refs/heads/repair-release-candidates',cwd=work)
            (release/'release.json').write_text('{}')
            amend_candidate()
            with self.assertRaisesRegex(ValueError,'invalid candidate manifest'):
                fetch_candidate(str(bare))
            (release/'release.json').write_text('{"revision":"bad"}')
            amend_candidate()
            with self.assertRaisesRegex(ValueError,'invalid candidate revision'):
                fetch_candidate(str(bare))
            (release/'release.json.sig').write_text('')
            amend_candidate()
            with self.assertRaisesRegex(ValueError,'candidate file exceeds'):
                fetch_candidate(str(bare))
            run('git','push','--force',str(bare),'HEAD:refs/heads/main',cwd=work)
            (release/'release.json').write_bytes(manifest)
            (release/'release.json.sig').write_bytes(signature)
            amend_candidate()
            with self.assertRaisesRegex(ValueError,'current main'):
                fetch_candidate(str(bare))

    def test_candidate_delivery_keeps_current_until_receiver_accepts(self):
        import hashlib
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from logbook.repair_release_delivery import accept_candidate
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.chmod(0o700)
            data = b'fixture package'
            manifest = json.dumps({'schema':2,'revision':'a'*40,'sequence':1,'bundle_sha256':hashlib.sha256(data).hexdigest()}).encode()
            signature = b'fixture signature'
            with patch('logbook.repair_release_delivery.send_release',return_value='UPDATED') as send:
                result = accept_candidate(root,manifest,signature,data)
                self.assertEqual(result['revision'],'a'*40)
                self.assertEqual(Path(result['bundle_path']).read_bytes(),data)
                send.assert_called_once_with(manifest,signature)
            with patch('logbook.repair_release_delivery.send_release',return_value='UNCHANGED'):
                self.assertEqual(accept_candidate(root,manifest,signature,data),result)
            (root/'delivery.lock').chmod(0o644)
            with self.assertRaisesRegex(ValueError,'private delivery lock'):
                accept_candidate(root,manifest,signature,data)
            (root/'delivery.lock').chmod(0o600)
            before = (root/'current.json').read_bytes()
            changed = json.dumps({'schema':2,'revision':'b'*40,'sequence':2,'bundle_sha256':hashlib.sha256(b'new').hexdigest()}).encode()
            with patch('logbook.repair_release_delivery.send_release',side_effect=ConnectionError('offline')):
                with self.assertRaises(ConnectionError):
                    accept_candidate(root,changed,signature,b'new')
            self.assertEqual((root/'current.json').read_bytes(),before)
            with self.assertRaises(ValueError):
                accept_candidate(root,manifest,signature,b'wrong')
            for bad in (b'',b'{'):
                with self.assertRaises(ValueError):
                    accept_candidate(root,bad,signature,data)
            package = Path(result['bundle_path'])
            package.write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError,'existing release package mismatch'):
                accept_candidate(root,manifest,signature,data)
            package.write_bytes(data)
            with patch('logbook.repair_release_delivery.send_release',return_value='UPDATED'), \
                 patch('logbook.repair_release_delivery.os.replace',side_effect=OSError('replace failed')):
                with self.assertRaisesRegex(OSError,'replace failed'):
                    accept_candidate(root,changed,signature,b'new')
            self.assertFalse(list(root.glob('.release-state-*')))

    def test_sender_uses_fixed_restricted_receiver_and_checks_response(self):
        from logbook.repair_release_delivery import send_release
        with patch('logbook.repair_release_delivery.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='UPDATED\n',stderr='')) as run:
            self.assertEqual(send_release(b'{}',b'signature'),'UPDATED')
            command = run.call_args.args[0]
            self.assertEqual(command[-2:],['logbook-repair@odin','sudo -n /usr/local/libexec/logbook-repair-release-receiver'])
            self.assertIn('StrictHostKeyChecking=yes',command)
        for response in (SimpleNamespace(returncode=1,stdout='',stderr='rejected'),SimpleNamespace(returncode=0,stdout='unexpected',stderr='')):
            with patch('logbook.repair_release_delivery.subprocess.run',return_value=response):
                with self.assertRaises(ConnectionError):
                    send_release(b'{}',b'signature')
        with patch('logbook.repair_release_delivery.subprocess.run',side_effect=subprocess.TimeoutExpired('ssh',15)):
            with self.assertRaises(TimeoutError):
                send_release(b'{}',b'signature')
        for manifest,signature in ((b'',b'sig'),(b'{}',b''),(b'\xff',b'sig')):
            with self.assertRaises(ValueError):
                send_release(manifest,signature)
