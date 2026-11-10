import json
from dataclasses import asdict, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from logbook.remote_config import (AgentConfig, ServerConfig, load_remote_config,
                                   private_address, read_secret)


class RemoteConfigTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.token = self.root / 'token'
        self.token.write_text('x' * 40)
        self.token.chmod(0o600)
        self.registry = self.root / 'devices'
        self.registry.write_text(json.dumps({'air-one': 'a' * 64}))
        self.registry.chmod(0o600)
        self.server = ServerConfig(self.root / 'db', self.root / 'intake', self.registry)
        self.agent = AgentConfig(self.root / 'buffer', self.root / 'SONY', 'SONY', 'uuid',
                                 'REC_FILE/FOLDER01', 'America/Los_Angeles',
                                 'http://127.0.0.1:8790', self.token)

    def test_load_configs_resolves_relative_paths(self):
        for config in (self.server, self.agent):
            path = self.root / 'config.json'
            values = asdict(config)
            for name, value in values.items():
                if isinstance(value, Path):
                    values[name] = str(value.relative_to(self.root))
            path.write_text(json.dumps(values))
            loaded = load_remote_config(path, type(config))
            self.assertEqual(loaded.root, config.root.resolve())
        self.server.validate()
        self.agent.validate()
        replace(self.server, bind_host='100.64.1.2', vpn_transport=True).validate()
        replace(self.agent, server_url='https://mimir.example').validate()
        replace(self.agent, server_url='http://100.64.1.2', vpn_transport=True).validate()
        self.assertFalse(private_address('8.8.8.8'))

    def test_server_rejects_unsafe_settings(self):
        for changes in ({'vpn_transport': 'false'}, {'bind_host': '0.0.0.0'}, {'bind_host': '192.168.1.2'},
                        {'max_bytes': 0}, {'reserve_bytes': -1}, {'port': 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.server, **changes).validate()
        for registry in ([], {'../bad': 'a'*64}, {'air': 'bad'}, {'air': 12},
                         {'a': 'a'*64, 'b': 'a'*64}):
            self.registry.write_text(json.dumps(registry))
            with self.assertRaises(ValueError):
                self.server.credentials()

    def test_ssh_transport_is_optional_and_loopback_only(self):
        replace(self.agent, ssh_host='jump-mimir', ssh_remote_port=18791).validate()
        for changes in ({'ssh_host': '-oProxyCommand=bad'}, {'ssh_host': 'jump mimir'},
                        {'ssh_host': None}, {'ssh_remote_port': 0}, {'ssh_remote_port': True},
                        {'ssh_remote_port': 65536}, {'server_url': 'http://localhost:18791'},
                        {'server_url': 'http://127.0.0.1'}, {'server_url': 'http://127.0.0.1:0'},
                        {'server_url': 'https://127.0.0.1:18791'},
                        {'server_url': 'http://100.64.0.1:18791', 'vpn_transport': True},
                        {'vpn_transport': True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.agent, **({'ssh_host': 'jump-mimir'} | changes)).validate()

    def test_agent_rejects_unsafe_settings_and_secret_permissions(self):
        for changes in ({'vpn_transport': 'false'}, {'server_url': 'ftp://host'}, {'server_url': 'https://a:b@host'},
                        {'server_url': 'https://host/path'}, {'server_url': 'http://8.8.8.8'},
                        {'volume_uuid': ''}, {'recordings_path': '../escape'},
                        {'recordings_path': '/absolute'}, {'stable_seconds': -1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.agent, **changes).validate()
        self.token.write_text('short')
        with self.assertRaises(ValueError):
            self.agent.validate()
        self.token.chmod(0o644)
        with self.assertRaises(ValueError):
            read_secret(self.token)
        link = self.root / 'link'
        link.symlink_to(self.token)
        with self.assertRaises(ValueError):
            read_secret(link)
