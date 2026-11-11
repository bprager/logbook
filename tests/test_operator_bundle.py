import hashlib
import io
import tarfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from logbook.operator_bundle_bootstrap import install_bundle


def archive(name, contents=b'fixture'):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as tar:
        info = tarfile.TarInfo(name)
        info.size = len(contents)
        tar.addfile(info, io.BytesIO(contents))
    return stream.getvalue()


class TrustedPathTests(unittest.TestCase):
    def test_rejects_symlink_writable_and_foreign_ancestors(self):
        import os
        import stat
        from types import SimpleNamespace
        from unittest.mock import patch
        from logbook.operator_bundle_bootstrap import trusted_parent
        good = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=os.geteuid())
        with patch('logbook.operator_bundle_bootstrap.os.lstat', return_value=good) as lookup:
            trusted_parent(Path('/safe/private/bundle'))
            self.assertEqual(lookup.call_count, 3)
            for bad in (SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=os.geteuid()),
                        SimpleNamespace(st_mode=stat.S_IFDIR | 0o777, st_uid=os.geteuid()),
                        SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=os.geteuid()+1)):
                lookup.side_effect = [good, bad]
                with self.assertRaisesRegex(ValueError, 'trusted'):
                    trusted_parent(Path('/safe/private/bundle'))
            with self.assertRaisesRegex(ValueError, 'trusted'):
                trusted_parent(Path('/private/../bundle'))


class OperatorBundleTests(unittest.TestCase):
    def test_standalone_installer_validates_policy_without_project_imports(self):
        import subprocess
        import sys
        from logbook import operator_bundle_bootstrap
        with TemporaryDirectory() as tmp:
            standalone = Path(tmp) / 'standalone-installer.py'
            standalone.write_bytes(Path(operator_bundle_bootstrap.__file__).read_bytes())
            code = (
                'import runpy,sys; '
                'ns=runpy.run_path(sys.argv[1]); '
                'print(ns["policy_plan_limit"]({"schema":3,"max_batch_records":500,'
                '"max_plan_records":5000,"expires_at":None}))'
            )
            result = subprocess.run([sys.executable, '-I', '-S', '-c', code, str(standalone)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), '5000')

    def test_executor_install_requires_owner_signature_and_exact_bytes(self):
        import json
        import subprocess
        import time
        from logbook.operator_bundle_bootstrap import install_protected_executor
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.chmod(0o700)
            key = root/'owner'
            subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
            allowed = root/'approval.allowed_signers'
            allowed.write_text('owner '+key.with_suffix('.pub').read_text())
            allowed.chmod(0o600)
            payload = archive('logbook/graph_maintenance_remote.py')
            document = {'schema':1, 'revision':'a'*40, 'bundle_sha256':hashlib.sha256(payload).hexdigest(),
                        'expires_at':int(time.time())+300}
            policy = root/'executor-approval.json'
            def sign():
                sig = root/'executor-approval.json.sig'
                sig.unlink(missing_ok=True)
                policy.write_text(json.dumps(document))
                policy.chmod(0o600)
                subprocess.run(['/usr/bin/ssh-keygen', '-Y', 'sign', '-n', 'logbook-repair-executor',
                                '-f', str(key), str(policy)], check=True, capture_output=True)
                sig.chmod(0o600)
            sign()
            root.chmod(0o755)
            with self.assertRaisesRegex(ValueError, 'directory'):
                install_protected_executor(payload, root)
            root.chmod(0o700)
            with self.assertRaises(ValueError):
                install_protected_executor(payload+b'changed', root)
            self.assertFalse((root/'executor').exists())
            document['expires_at'] = 0
            sign()
            with self.assertRaises(ValueError):
                install_protected_executor(payload, root)
            document['expires_at'] = int(time.time())+300
            sign()
            policy.write_text(policy.read_text()+' ')
            with self.assertRaises(ValueError):
                install_protected_executor(payload, root)
            policy.write_text('{')
            (root/'executor-approval.json.sig').unlink()
            subprocess.run(['/usr/bin/ssh-keygen', '-Y', 'sign', '-n', 'logbook-repair-executor',
                            '-f', str(key), str(policy)], check=True, capture_output=True)
            (root/'executor-approval.json.sig').chmod(0o600)
            with self.assertRaisesRegex(ValueError, 'invalid executor approval'):
                install_protected_executor(payload, root)
            sign()
            install_protected_executor(payload, root)
            self.assertEqual((root/'executor/logbook/graph_maintenance_remote.py').read_bytes(), b'fixture')
            with self.assertRaises(FileExistsError):
                install_protected_executor(payload, root)

    def setUp(self):
        from unittest.mock import patch
        self.trust = patch('logbook.operator_bundle_bootstrap.trusted_parent')
        self.trust.start()
        self.addCleanup(self.trust.stop)

    def test_installs_private_verified_bundle_once(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = archive('logbook/example.py')
            destination = root / 'bundle'
            install_bundle(payload, destination, hashlib.sha256(payload).hexdigest())
            self.assertEqual((destination/'logbook/example.py').read_bytes(), b'fixture')
            self.assertTrue((destination/'.bundle-ready').exists())
            with self.assertRaises(FileExistsError):
                install_bundle(payload, destination, hashlib.sha256(payload).hexdigest())

    def test_install_syncs_contents_and_directories_before_return(self):
        import os
        import stat
        from unittest.mock import patch
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = archive('logbook/nested/example.py')
            synced = []
            real_fsync = os.fsync
            def record_sync(fd):
                info = os.fstat(fd)
                synced.append((stat.S_ISDIR(info.st_mode), info.st_size))
                real_fsync(fd)
            with patch('logbook.operator_bundle_bootstrap.os.fsync', side_effect=record_sync):
                install_bundle(payload, root/'bundle', hashlib.sha256(payload).hexdigest())
            files = [size for is_dir, size in synced if not is_dir]
            self.assertIn(len(b'fixture'), files)
            self.assertIn(65, files)
            self.assertGreaterEqual(sum(is_dir for is_dir, _ in synced), 3)
            self.assertTrue(synced[-1][0])

    def test_sync_failure_does_not_accept_partial_bundle(self):
        from unittest.mock import patch
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = archive('logbook/example.py')
            with patch('logbook.operator_bundle_bootstrap.os.fsync', side_effect=OSError('sync failed')):
                with self.assertRaisesRegex(OSError, 'sync failed'):
                    install_bundle(payload, root/'bundle', hashlib.sha256(payload).hexdigest())
            self.assertFalse((root/'bundle').exists())

    def test_rejects_wrong_digest_and_unsafe_paths(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = archive('../escape')
            with self.assertRaisesRegex(ValueError, 'digest'):
                install_bundle(payload, root/'bad', '0'*64)
            with self.assertRaisesRegex(ValueError, 'path'):
                install_bundle(payload, root/'bad', hashlib.sha256(payload).hexdigest())
            self.assertFalse((root/'bad').exists())

    def test_bundle_builder_accepts_only_known_packages(self):
        from logbook.operator_bundle import build_bundle
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            packages = {}
            for name in ('logbook','neo4j','pytz'):
                path = root/name
                path.mkdir()
                (path/'__init__.py').write_text('')
                packages[name] = path
            data = build_bundle(packages)
            install_bundle(data, root/'installed', hashlib.sha256(data).hexdigest())
            self.assertTrue((root/'installed/neo4j/__init__.py').exists())
            with self.assertRaisesRegex(ValueError, 'packages'):
                build_bundle({'unexpected':root})

    def test_installer_rejects_links_and_nonprivate_parent(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode='w') as tar:
                info = tarfile.TarInfo('logbook/link')
                info.type = tarfile.SYMTYPE
                info.linkname = '/etc/passwd'
                tar.addfile(info)
            payload = stream.getvalue()
            with self.assertRaisesRegex(ValueError, 'path or type'):
                install_bundle(payload, root/'bad', hashlib.sha256(payload).hexdigest())
            root.chmod(0o755)
            valid = archive('logbook/file')
            with self.assertRaisesRegex(ValueError, 'private'):
                install_bundle(valid, root/'bad', hashlib.sha256(valid).hexdigest())
            root.chmod(0o700)

    def test_ssh_provisioning_requires_explicit_success(self):
        from subprocess import CompletedProcess
        from unittest.mock import patch
        from logbook.operator_bundle import provision_bundle
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as tar:
            info = tarfile.TarInfo('logbook/operator_bundle_bootstrap.py')
            payload = b'example_bootstrap'
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        data = stream.getvalue()
        with patch('logbook.operator_bundle.subprocess.run') as run:
            run.return_value = CompletedProcess([], 0, b'BUNDLE_INSTALLED\n', b'')
            digest = provision_bundle('odin', Path('/key'), Path('/private/bundle'), Path('/private/stage'), data)
            self.assertEqual(digest, hashlib.sha256(data).hexdigest())
            self.assertIn('example_bootstrap', run.call_args.args[0][-1])
            run.return_value = CompletedProcess([], 0, b'NO', b'')
            with self.assertRaisesRegex(RuntimeError, 'response'):
                provision_bundle('odin', Path('/key'), Path('/private/bundle'), Path('/private/stage'), data)
        with self.assertRaisesRegex(ValueError, 'sibling'):
            provision_bundle('odin', Path('/key'), Path('/private/bundle'), Path('/other/stage'), b'data')
        bad = io.BytesIO()
        with tarfile.open(fileobj=bad, mode='w:gz') as tar:
            info = tarfile.TarInfo('logbook/operator_bundle_bootstrap.py')
            info.type = tarfile.DIRTYPE
            tar.addfile(info)
        with self.assertRaisesRegex(ValueError, 'bootstrap'):
            provision_bundle('odin', Path('/key'), Path('/private/bundle'), Path('/private/stage'), bad.getvalue())
        with patch('tarfile.TarFile.extractfile', return_value=None):
            with self.assertRaisesRegex(ValueError, 'bootstrap'):
                provision_bundle('odin', Path('/key'), Path('/private/bundle'), Path('/private/stage'), data)

    def test_bundle_builder_excludes_cache_and_rejects_links(self):
        from logbook.operator_bundle import build_bundle
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            packages = {}
            for name in ('logbook','neo4j','pytz'):
                path = root/name
                path.mkdir()
                (path/'__init__.py').write_text('')
                packages[name] = path
            cache = packages['logbook']/'__pycache__'
            cache.mkdir()
            (cache/'x.pyc').write_bytes(b'cache')
            build_bundle(packages)
            (packages['logbook']/'link').symlink_to('/etc/passwd')
            with self.assertRaisesRegex(ValueError, 'file type'):
                build_bundle(packages)

    def test_bundle_size_limit_and_incomplete_archive(self):
        from unittest.mock import MagicMock, patch
        from logbook.operator_bundle import build_bundle
        payload = archive('logbook/example.py')
        fake = MagicMock()
        fake.__enter__.return_value = fake
        oversized = tarfile.TarInfo('logbook/huge')
        oversized.size = 60000001
        fake.getmembers.return_value = [oversized]
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('logbook.operator_bundle_bootstrap.tarfile.open', return_value=fake):
                with self.assertRaisesRegex(ValueError, 'too large'):
                    install_bundle(payload, root/'bad', hashlib.sha256(payload).hexdigest())
                file_info = tarfile.TarInfo('logbook/example.py')
                fake.getmembers.return_value = [file_info]
                fake.extractfile.return_value = None
                with self.assertRaisesRegex(ValueError, 'missing bundle'):
                    install_bundle(payload, root/'bad', hashlib.sha256(payload).hexdigest())
                self.assertFalse((root/'bad').exists())
        fake_buffer = MagicMock()
        fake_buffer.getvalue.return_value = b'x'*30000001
        with patch('logbook.operator_bundle.io.BytesIO', return_value=fake_buffer), \
             patch('logbook.operator_bundle.tarfile.open'):
            with self.assertRaisesRegex(ValueError, 'size limit'):
                build_bundle({name:Path('/unused') for name in ('logbook','neo4j','pytz')})

class RevisionBundleTests(unittest.TestCase):
    def test_uses_exact_commit_not_mutable_checkout(self):
        import subprocess
        import zipfile
        from unittest.mock import patch
        from logbook.operator_bundle import build_revision_bundle
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            source = root/'src/logbook'
            source.mkdir(parents=True)
            (source/'__init__.py').write_text('reviewed')
            subprocess.run(['git', '-C', str(root), 'add', 'src'], check=True)
            subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test', '-c',
                            'user.email=test@example.invalid', 'commit', '-qm', 'fixture'], check=True)
            revision = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
            (source/'__init__.py').write_text('changed')
            pins = {}
            for name in ('neo4j', 'pytz'):
                path = root/(name+'.whl')
                with zipfile.ZipFile(path, 'w') as wheel:
                    wheel.writestr(name+'/__init__.py', name)
                pins[name] = (path.name, hashlib.sha256(path.read_bytes()).hexdigest())
            pin_patch = patch('logbook.operator_bundle.PINNED_WHEELS', pins)
            pin_patch.start()
            self.addCleanup(pin_patch.stop)
            with patch('gzip.time.time', return_value=100):
                payload = build_revision_bundle(root, revision, root)
            with patch('gzip.time.time', return_value=200):
                self.assertEqual(payload, build_revision_bundle(root, revision, root))
            with tarfile.open(fileobj=io.BytesIO(payload), mode='r:gz') as tar:
                self.assertEqual(tar.extractfile('logbook/__init__.py').read(), b'reviewed')
            from unittest.mock import patch
            with patch('logbook.operator_bundle_bootstrap.trusted_parent'):
                install_bundle(payload, root/'installed', hashlib.sha256(payload).hexdigest())
            self.assertEqual((root/'installed/logbook/__init__.py').read_text(), 'reviewed')
            with self.assertRaisesRegex(ValueError, 'revision'):
                build_revision_bundle(root, 'HEAD', root)
            blob = subprocess.check_output(['git', '-C', str(root), 'hash-object', '-w',
                                            'src/logbook/__init__.py'], text=True).strip()
            with self.assertRaisesRegex(ValueError, 'commit'):
                build_revision_bundle(root, blob, root)
            (source/'bad').symlink_to('/etc/passwd')
            subprocess.run(['git', '-C', str(root), 'add', 'src'], check=True)
            subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test', '-c',
                            'user.email=test@example.invalid', 'commit', '-qm', 'link'], check=True)
            unsafe = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
            with self.assertRaisesRegex(ValueError, 'source'):
                build_revision_bundle(root, unsafe, root)

class CheckoutVerificationTests(unittest.TestCase):
    def test_rejects_changed_or_untracked_source(self):
        import subprocess
        from logbook.operator_bundle import verify_checkout
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            source = root/'src/logbook'
            source.mkdir(parents=True)
            file = source/'entry.py'
            file.write_text('committed')
            subprocess.run(['git', '-C', str(root), 'add', 'src'], check=True)
            subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test', '-c',
                            'user.email=test@example.invalid', 'commit', '-qm', 'fixture'], check=True)
            revision = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
            verify_checkout(root, revision)
            with self.assertRaisesRegex(ValueError, 'revision'):
                verify_checkout(root, '--help')
            file.write_text('modified')
            with self.assertRaisesRegex(ValueError, 'checkout'):
                verify_checkout(root, revision)
            file.write_text('committed')
            (source/'untracked.py').write_text('new')
            with self.assertRaisesRegex(ValueError, 'checkout'):
                verify_checkout(root, revision)

class PinnedWheelTests(unittest.TestCase):
    def test_rejects_wheel_changed_after_pin(self):
        import zipfile
        from unittest.mock import patch
        from logbook.operator_bundle import add_pinned_wheels
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            wheel = root/'fixture.whl'
            with zipfile.ZipFile(wheel, 'w') as archive:
                archive.writestr('neo4j/', '')
                archive.writestr('neo4j-0.dist-info/METADATA', 'metadata')
                archive.writestr('neo4j/__init__.py', 'trusted')
            pins = {'neo4j':('fixture.whl', hashlib.sha256(wheel.read_bytes()).hexdigest())}
            output = io.BytesIO()
            with patch('logbook.operator_bundle.PINNED_WHEELS', pins):
                with tarfile.open(fileobj=output, mode='w') as tar:
                    add_pinned_wheels(tar, root)
                with tarfile.open(fileobj=io.BytesIO(output.getvalue()), mode='r') as tar:
                    self.assertEqual(tar.extractfile('neo4j/__init__.py').read(), b'trusted')
                wheel.write_bytes(b'changed')
                with tarfile.open(fileobj=io.BytesIO(), mode='w') as tar:
                    with self.assertRaisesRegex(ValueError, 'checksum'):
                        add_pinned_wheels(tar, root)
            with zipfile.ZipFile(wheel, 'w') as archive:
                archive.writestr('neo4j/../escape.py', 'unsafe')
            pins['neo4j'] = ('fixture.whl', hashlib.sha256(wheel.read_bytes()).hexdigest())
            with patch('logbook.operator_bundle.PINNED_WHEELS', pins):
                with tarfile.open(fileobj=io.BytesIO(), mode='w') as tar:
                    with self.assertRaisesRegex(ValueError, 'member'):
                        add_pinned_wheels(tar, root)
