import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from fastapi.testclient import TestClient

from logbook.config import load_app_config
from logbook.ledger import open_ledger
from logbook.observer import build_observer_snapshot, observer_snapshot_from_dict, render_observer_snapshot
from logbook.remote_api import create_ingest_app
from logbook.remote_config import ServerConfig
from logbook.remote_status import read_remote_status
from test_process_mounted_recorder import _write_env


class RemoteStatusTests(TestCase):
    def test_redacted_device_status_round_trip_via_existing_observer(self):
        from hashlib import sha256
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_app_config(_write_env(root))
            secret = root / 'devices'
            secret.write_text(json.dumps({'air-one': sha256(b'x' * 40).hexdigest()}))
            secret.chmod(0o600)
            client = TestClient(create_ingest_app(ServerConfig(config.sqlite_path,
                                root / 'intake', secret, reserve_bytes=0)))
            headers = {'Authorization': 'Bearer ' + 'x' * 40}
            status = {'pending_bytes': 1234, 'retries': 3, 'failure': 'transfer_unavailable',
                      'storage_pressure': True}
            self.assertEqual(client.post('/ingest/status', json=status, headers=headers).status_code, 200)
            for field, value in (('failure', '/private/token'), ('pending_bytes', -1),
                                 ('retries', True), ('storage_pressure', 'yes')):
                self.assertEqual(client.post('/ingest/status', json={**status, field: value},
                                             headers=headers).status_code, 400)
            snapshot = build_observer_snapshot(config)
            remote = snapshot.to_dict()['remote_ingest']
            self.assertEqual(remote['devices'][0]['pending_bytes'], 1234)
            self.assertEqual(remote['devices'][0]['retries'], 3)
            self.assertEqual(remote['audit'][0]['code'], 'invalid_status')
            rendered = json.dumps(remote)
            for sensitive in ('token', 'source_path', str(root), 'x' * 40):
                self.assertNotIn(sensitive, rendered)
            self.assertEqual(observer_snapshot_from_dict(snapshot.to_dict()).remote_ingest, remote)
            self.assertIn('Remote ingest', render_observer_snapshot(snapshot))
            self.assertEqual(read_remote_status(root / 'absent')['state'], 'unavailable')
            old = root / 'old.sqlite'
            import sqlite3
            sqlite3.connect(old).close()
            self.assertEqual(read_remote_status(old)['state'], 'unavailable')
            # Observation never initializes/migrates an old ledger.
            db = sqlite3.connect(old)
            self.assertEqual(db.execute('SELECT count(*) FROM sqlite_master').fetchone()[0], 0)
            db.close()
            ledger = open_ledger(config.sqlite_path)
            ledger.close()
            self.assertIsNone(build_observer_snapshot(replace(config, sqlite_path=root/'missing')).remote_ingest)
