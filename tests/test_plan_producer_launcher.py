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
    def test_release_delivery_service_uses_restricted_account(self):
        import plistlib
        path = Path(__file__).resolve().parents[1]/'deploy/local.logbook.repair-release.plist'
        service = plistlib.loads(path.read_bytes())
        self.assertEqual(service['UserName'],'logbook-repair-operator')
        self.assertEqual(service['ProgramArguments'],['/usr/local/libexec/logbook-plan-producer'])
        self.assertEqual(service['StartInterval'],300)

    def test_restricted_operator_launches_release_delivery(self):
        module = launcher_module()
        users = {'bernd':1,'logbook-plan-producer':2,'logbook-repair-operator':3}
        with patch.object(module,'check_trusted_directory'), patch.object(module,'check_trusted_file'), \
             patch.object(module.os,'walk',return_value=[]), patch.object(module.os,'geteuid',return_value=3), \
             patch.object(module.pwd,'getpwnam',side_effect=lambda name: SimpleNamespace(pw_uid=users[name])), \
             patch('logbook.repair_release_delivery.main') as run, patch.object(module.sys,'path',list(module.sys.path)), \
             patch.object(module.sys,'dont_write_bytecode',False), patch.object(module.sys,'argv',['launcher']):
            module.main()
            run.assert_called_once_with()

    def test_restricted_operator_can_run_only_fixed_repair_cycle(self):
        import plistlib
        module = launcher_module()
        users = {'bernd':1,'logbook-plan-producer':2,'logbook-repair-operator':3}
        path = Path(__file__).resolve().parents[1]/'deploy/local.logbook.repair-cycle.plist'
        service = plistlib.loads(path.read_bytes())
        self.assertEqual(service['UserName'],'logbook-repair-operator')
        self.assertEqual(service['ProgramArguments'],['/usr/local/libexec/logbook-plan-producer','repair-cycle'])
        self.assertEqual(service['StartInterval'],120)
        with patch.object(module,'check_trusted_directory'), patch.object(module,'check_trusted_file'), \
             patch.object(module.os,'walk',return_value=[]), patch.object(module.os,'geteuid',return_value=3), \
             patch.object(module.pwd,'getpwnam',side_effect=lambda name: SimpleNamespace(pw_uid=users[name])), \
             patch('logbook.repair_cycle.main') as run, patch.object(module.sys,'path',list(module.sys.path)), \
             patch.object(module.sys,'dont_write_bytecode',False), patch.object(module.sys,'argv',['launcher','repair-cycle']):
            module.main()
            run.assert_called_once_with()
            module.sys.argv=['launcher','unexpected']
            with self.assertRaises(ValueError):
                module.main()

    def test_private_configuration_does_not_share_root_owned_code_directory(self):
        from logbook.plan_producer import KEY
        module = launcher_module()
        self.assertNotEqual(KEY.parent, module.CODE_ROOT.parent)
        self.assertNotEqual(KEY.parent, module.CODE_ROOT)

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
