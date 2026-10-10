import json
from dataclasses import asdict
from hashlib import sha256
from unittest.mock import patch

from fastapi.testclient import TestClient

from test_remote_store import RemoteStoreTests
from logbook.remote_api import create_ingest_app
from logbook.remote_config import ServerConfig


class RemoteApiTests(RemoteStoreTests):
    def setUp(self):
        super().setUp()
        self.credentials = self.root / 'devices.json'
        self.credentials.write_text(json.dumps({'air-one': sha256(b'a' * 40).hexdigest(),
                                                 'air-two': sha256(b'b' * 40).hexdigest()}))
        self.credentials.chmod(0o600)
        self.config = ServerConfig(self.db, self.root / 'remote', self.credentials,
                                   reserve_bytes=0)
        self.client = TestClient(create_ingest_app(self.config))
        self.headers = {'Authorization': 'Bearer ' + 'a' * 40}

    def test_protocol_and_revocation(self):
        meta = asdict(self.meta)
        response = self.client.post('/ingest/recordings/check', json=meta)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.client.post('/ingest/recordings/check', json=meta,
                                         headers=self.headers).json()['state'], 'missing')
        upload = self.client.post('/ingest/uploads', json=meta, headers=self.headers).json()
        url = '/ingest/uploads/' + upload['upload_id']
        headers = {**self.headers, 'X-Offset': '0', 'X-Chunk-SHA256': self.meta.sha256}
        self.assertEqual(self.client.put(url, content=self.data, headers=headers).status_code, 200)
        receipt = self.client.post(url + '/complete', headers=self.headers).json()
        self.assertEqual(receipt['state'], 'present')
        self.assertEqual(self.client.post(url + '/complete', headers=self.headers).json(), receipt)
        self.credentials.write_text('{}')
        self.assertEqual(self.client.post('/ingest/recordings/check', json=meta,
                                         headers=self.headers).status_code, 401)

    def test_bounded_invalid_requests(self):
        for payload in ({}, {**asdict(self.meta), 'filename': '../secret.mp3'},
                        {**asdict(self.meta), 'path': '/secret'}):
            self.assertEqual(self.client.post('/ingest/uploads', json=payload,
                                             headers=self.headers).status_code, 400)
        self.assertEqual(self.client.post('/ingest/uploads', content=b' ' * 9000,
                                         headers=self.headers).status_code, 413)
        self.assertEqual(self.client.put('/ingest/uploads/nope', content=b'x',
                                        headers=self.headers).status_code, 400)
        with patch('logbook.remote_api.time.time', return_value=100):
            for _ in range(self.config.requests_per_minute):
                self.client.post('/ingest/recordings/check', json=asdict(self.meta),
                                 headers=self.headers)
            self.assertEqual(self.client.post('/ingest/recordings/check', json=asdict(self.meta),
                                             headers=self.headers).status_code, 429)

    def test_error_codes_and_secret_redaction(self):
        self.assertEqual(self.client.post('/ingest/uploads/unknown/complete',
                                         headers=self.headers).status_code, 404)
        self.assertEqual(self.client.put('/ingest/uploads/unknown', headers={**self.headers,
                                         'X-Offset': 'not-number'}, content=b'x').status_code, 400)
        with patch('logbook.remote_store.shutil.disk_usage', side_effect=OSError('secret path')):
            response = self.client.post('/ingest/uploads', json=asdict(self.meta),
                                        headers=self.headers)
        self.assertEqual(response.status_code, 507)
        self.assertNotIn('secret', response.text)
        self.credentials.unlink()
        self.assertEqual(self.client.post('/ingest/uploads', headers=self.headers).status_code, 503)
