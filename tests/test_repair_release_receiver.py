import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch


class RepairReleaseReceiverTests(TestCase):
    def test_receiver_request_accepts_only_bounded_metadata(self):
        import json
        from logbook.operator_bundle_bootstrap import receive_release_request
        with patch('logbook.operator_bundle_bootstrap.receive_release', return_value='UPDATED') as receive:
            self.assertEqual(receive_release_request(Path('/guard'),json.dumps({'manifest':'{}','signature':'sig'}).encode()), 'UPDATED')
            receive.assert_called_once_with(Path('/guard'), b'{}', b'sig')
            for raw in (b'{}', b'{', b'[]', b'{"manifest":1,"signature":"x"}', b'x'*200001):
                with self.assertRaises(ValueError):
                    receive_release_request(Path('/guard'),raw)

    def test_signed_updates_are_ordered_idempotent_and_recoverable(self):
        from logbook.operator_bundle_bootstrap import approved_release, receive_release
        from logbook.repair_release_publisher import publish_release
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.chmod(0o700)
            key = root/'signer'
            subprocess.run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True)
            allowed = root/'release.allowed_signers'
            allowed.write_text('release '+key.with_suffix('.pub').read_text())
            allowed.chmod(0o600)
            def make(sequence, data):
                out = root/f'candidate-{sequence}-{data.decode()}'
                publish_release(data, 'a'*40, key, out, sequence=sequence)
                return ((out/'release.json').read_bytes(),(out/'release.json.sig').read_bytes())
            first = make(1,b'one')
            second = make(2,b'two')
            conflict = make(2,b'other')
            with patch('logbook.operator_bundle_bootstrap.trusted_parent'), \
                 patch('logbook.operator_bundle_bootstrap.verify_protected_executor'):
                with self.assertRaisesRegex(ValueError,'bounded'):
                    receive_release(root,b'',b'signature')
                self.assertEqual(receive_release(root,*first), 'UPDATED')
                (root/'releases').chmod(0o755)
                with self.assertRaisesRegex(ValueError,'private releases'):
                    receive_release(root,*first)
                (root/'releases').chmod(0o700)
                (root/'release-update.lock').chmod(0o644)
                with self.assertRaisesRegex(ValueError,'private release lock'):
                    receive_release(root,*first)
                (root/'release-update.lock').chmod(0o600)
                self.assertEqual(receive_release(root,*first), 'UNCHANGED')
                self.assertEqual(approved_release(root)['sequence'],1)
                pending = root/'releases'/'2'
                shutil.copytree(root/'candidate-2-two',pending)
                self.assertEqual(receive_release(root,*second), 'UPDATED')
                self.assertEqual(approved_release(root)['sequence'],2)
                for bad in (first, conflict, (second[0]+b' ',second[1])):
                    with self.assertRaises(ValueError):
                        receive_release(root,*bad)
                self.assertEqual(approved_release(root)['sequence'],2)
                import json
                legacy = root/'legacy.json'
                legacy.write_text(json.dumps({'schema':1,'revision':'a'*40,'bundle_sha256':'b'*64}))
                subprocess.run(['/usr/bin/ssh-keygen','-Y','sign','-n','logbook-repair-release','-f',str(key),str(legacy)],check=True,capture_output=True)
                with self.assertRaisesRegex(ValueError,'ordered release'):
                    receive_release(root,legacy.read_bytes(),Path(str(legacy)+'.sig').read_bytes())
                third = make(3,b'three')
                make(3,b'conflict')
                shutil.copytree(root/'candidate-3-conflict',root/'releases/3')
                with self.assertRaisesRegex(ValueError,'generation conflicts'):
                    receive_release(root,*third)
                shutil.rmtree(root/'releases/3')
                with patch('logbook.operator_bundle_bootstrap.os.replace',side_effect=OSError('replace failed')):
                    with self.assertRaisesRegex(OSError,'replace failed'):
                        receive_release(root,*third)
                self.assertEqual(approved_release(root)['sequence'],2)
                self.assertEqual(receive_release(root,*third),'UPDATED')
                pointer = root/'release.current'
                pointer.write_text('../bad\n')
                with self.assertRaisesRegex(ValueError,'invalid release pointer'):
                    approved_release(root)
                pointer.write_text('3\n')
                shutil.copytree(root/'candidate-1-one',root/'releases/9')
                with self.assertRaisesRegex(ValueError,'generation mismatch'):
                    approved_release(root,root/'releases/9')
