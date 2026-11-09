import importlib.machinery
import importlib.util
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch


def launcher_module():
    path = Path(__file__).resolve().parents[1]/'scripts/logbook-plan-producer'
    loader = importlib.machinery.SourceFileLoader('fixture_producer_launcher', str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ProducerLauncherTests(TestCase):
    def test_private_configuration_does_not_share_root_owned_code_directory(self):
        from logbook.plan_producer import CONFIG, KEY
        module = launcher_module()
        self.assertEqual(CONFIG.parent, KEY.parent)
        self.assertNotEqual(CONFIG.parent, module.CODE_ROOT.parent)
        self.assertNotEqual(CONFIG.parent, module.CODE_ROOT)

    def test_rejects_writable_symlinked_or_non_root_code(self):
        module = launcher_module()
        safe = SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o755)
        for bad in (SimpleNamespace(st_uid=501, st_mode=stat.S_IFDIR | 0o755),
                    SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o777),
                    SimpleNamespace(st_uid=0, st_mode=stat.S_IFLNK | 0o777)):
            with self.subTest(mode=bad.st_mode, uid=bad.st_uid), \
                 patch.object(Path, 'lstat', return_value=bad), self.assertRaises(ValueError):
                module.check_trusted_directory(Path('/root-controlled/code'))
        with patch.object(Path, 'lstat', return_value=safe):
            module.check_trusted_directory(Path('/root-controlled/code'))

    def test_rejects_untrusted_files_before_import(self):
        module = launcher_module()
        regular = SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o644)
        with patch.object(Path,'lstat',return_value=regular):
            module.check_trusted_file(Path('/protected/module.py'))
        for bad in (SimpleNamespace(st_uid=os.getuid()+1,st_mode=stat.S_IFREG|0o644),
                    SimpleNamespace(st_uid=0,st_mode=stat.S_IFREG|0o666),
                    SimpleNamespace(st_uid=0,st_mode=stat.S_IFLNK|0o777)):
            with patch.object(Path,'lstat',return_value=bad), self.assertRaises(ValueError):
                module.check_trusted_file(Path('/protected/module.py'))
