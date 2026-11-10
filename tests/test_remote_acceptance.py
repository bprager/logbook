import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from logbook.ledger import open_ledger
from logbook.remote_agent import run_agent
from logbook.remote_api import create_ingest_app
from logbook.remote_config import AgentConfig, ServerConfig
from logbook.remote_status import read_remote_status


class RemoteAcceptanceTests(TestCase):
    def test_two_macs_lost_receipt_restart_and_same_hash_use_one_canonical_job(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            folder = root / 'SONY' / 'REC_FILE' / 'FOLDER01'
            folder.mkdir(parents=True)
            original = folder / '261009_0830.mp3'
            original.write_bytes(b'synthetic complete MP3 bytes')
            registry = root / 'devices'
            registry.write_text(json.dumps({name: sha256(char.encode() * 40).hexdigest()
                                           for name, char in [('air-one', 'a'), ('air-two', 'b')]}))
            registry.chmod(0o600)
            server = ServerConfig(root / 'ledger.sqlite', root / 'intake', registry, reserve_bytes=0)
            configs = []
            for name, char in [('air-one', 'a'), ('air-two', 'b')]:
                token = root / (name + '.token')
                token.write_text(char * 40)
                token.chmod(0o600)
                configs.append(AgentConfig(root / name, root / 'SONY', 'SONY', 'uuid',
                                           'REC_FILE/FOLDER01', 'America/Los_Angeles',
                                           'http://127.0.0.1:8790', token,
                                           reserve_bytes=0, stable_seconds=0, chunk_bytes=4))
            client = TestClient(create_ingest_app(server), headers={'Authorization': 'Bearer ' + 'a'*40})
            real_post = client.post
            def lost_receipt(url, **kwargs):
                result = real_post(url, **kwargs)
                if url.endswith('/complete'):
                    raise httpx.ReadError('lost successful response')
                return result
            with patch('logbook.remote_agent.validate_volume', return_value=folder):
                with patch.object(client, 'post', side_effect=lost_receipt):
                    first = run_agent(configs[0], client=client, now=100)
                self.assertEqual(first['pending_bytes'], original.stat().st_size)
                self.assertEqual(first['retries'], 1)
                # Simulate both service and agent restart; the second Mac uses its own credential.
                client = TestClient(create_ingest_app(server), headers={'Authorization': 'Bearer ' + 'b'*40})
                self.assertEqual(run_agent(configs[1], client=client)['pending_bytes'], 0)
                client.headers['Authorization'] = 'Bearer ' + 'a'*40
                self.assertEqual(run_agent(configs[0], client=client, now=200)['pending_bytes'], 0)
            ledger = open_ledger(server.ledger_path)
            self.assertEqual(ledger.connection.execute('SELECT count(*) FROM recording_jobs').fetchone()[0], 1)
            job = ledger.get_by_id(1)
            self.assertEqual(Path(job.copied_path).read_bytes(), original.read_bytes())
            self.assertEqual(job.parsed_recorded_at, '2026-10-09T08:30:00')
            ledger.close()
            self.assertEqual(len(read_remote_status(server.ledger_path)['devices']), 2)
            self.assertTrue(original.exists())
            self.assertEqual(len(list(root.glob('air-*/spool/*/audio.mp3'))), 2)
            # Revocation takes effect without service restart, even with a saved local credential.
            registry.write_text('{}')
            self.assertEqual(client.post('/ingest/uploads', json={}).status_code, 401)
            with self.assertRaises(ValueError):
                create_ingest_app(replace(server, bind_host='0.0.0.0', vpn_transport=True))
