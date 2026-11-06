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
        with patch('logbook.operator_bundle.subprocess.run') as run:
            run.return_value = CompletedProcess([], 0, b'BUNDLE_INSTALLED\n', b'')
            digest = provision_bundle('odin', Path('/key'), Path('/private/bundle'), Path('/private/stage'), b'data')
            self.assertEqual(digest, hashlib.sha256(b'data').hexdigest())
            run.return_value = CompletedProcess([], 0, b'NO', b'')
            with self.assertRaisesRegex(RuntimeError, 'response'):
                provision_bundle('odin', Path('/key'), Path('/private/bundle'), Path('/private/stage'), b'data')
        with self.assertRaisesRegex(ValueError, 'sibling'):
            provision_bundle('odin', Path('/key'), Path('/private/bundle'), Path('/other/stage'), b'data')

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
