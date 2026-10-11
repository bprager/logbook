import plistlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from logbook.service_install import write_package, main


class ServiceInstallTests(TestCase):
    def test_boot_package_uses_unprivileged_owner_and_one_ledger(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                write_package(root / '.env', root / 'server.json', root, root / 'package', '-bad')
            config = SimpleNamespace(processing_root=root, sqlite_path=root / 'ledger',
                                     api=SimpleNamespace(bind_host='0.0.0.0', port=8788),
                                     odin=SimpleNamespace(api_base_url='http://odin:8765'))
            server = SimpleNamespace(ledger_path=config.sqlite_path, port=18791)
            with patch('logbook.service_install.load_app_config', return_value=config), \
                    patch('logbook.service_install.load_remote_config', return_value=server):
                settings, path = write_package(root / '.env', root / 'server.json', root,
                                               root / 'package', 'odin')
                import json
                specs = json.loads(settings.read_text())['services']
                self.assertEqual({s['name'] for s in specs}, {'api', 'receiver', 'queue', 'odin'})
                daemon = plistlib.loads(path.read_bytes())
                self.assertNotEqual(daemon['UserName'], 'root')
                self.assertTrue(daemon['RunAtLoad'])
                self.assertEqual(daemon['ExitTimeOut'], 60)
                self.assertTrue(daemon['KeepAlive'])
                self.assertNotIn('AbandonProcessGroup', daemon)
                self.assertEqual(settings.stat().st_mode & 0o777, 0o600)
                self.assertTrue(all('cleanup' not in str(s) for s in specs))
                main(['--env', str(root / '.env'), '--server-config', str(root / 'server.json'),
                      '--output', str(root / 'package')])
                server.ledger_path = root / 'other-ledger'
                with self.assertRaises(ValueError):
                    write_package(root / '.env', root / 'server.json', root, root / 'package', 'odin')
                server.ledger_path = config.sqlite_path
                config.api = None
                with self.assertRaises(ValueError):
                    write_package(root / '.env', root / 'server.json', root, root / 'package', 'odin')
