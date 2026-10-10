import json
import plistlib
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import httpx

from logbook.remote_agent import Outbox, run_agent, validate_volume
from logbook.remote_config import AgentConfig


class RemoteAgentTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        mount = self.root / 'IC RECORDER'
        self.folder = mount / 'REC_FILE' / 'FOLDER01'
        self.folder.mkdir(parents=True)
        token = self.root / 'token'
        token.write_text('x' * 40)
        token.chmod(0o600)
        self.config = AgentConfig(self.root / 'outbox', mount, 'IC RECORDER', 'sony-uuid',
                                  'REC_FILE/FOLDER01', 'America/Los_Angeles',
                                  'http://127.0.0.1:8790', token, stable_seconds=0, reserve_bytes=0)
        self.audio = self.folder / '261009_0830.mp3'
        self.audio.write_bytes(b'test audio')
        self.volume = {'VolumeName': 'IC RECORDER', 'VolumeUUID': 'sony-uuid',
                       'MountPoint': str(mount), 'Internal': False, 'BusProtocol': 'USB'}

    def test_volume_identity_sidecars_and_symlinks(self):
        with patch('logbook.remote_agent.subprocess.run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = plistlib.dumps(self.volume)
            self.assertEqual(validate_volume(self.config), self.folder)
            run.return_value.stdout = plistlib.dumps({**self.volume, 'VolumeUUID': 'wrong'})
            with self.assertRaises(ValueError):
                validate_volume(self.config)
        (self.folder / '._261009_0830.mp3').write_bytes(b'sidecar')
        (self.folder / 'link.mp3').symlink_to(self.audio)
        box = Outbox(self.config)
        self.addCleanup(box.close)
        box.snapshot(self.folder)
        self.assertEqual(len(box.pending(now=10**12)), 1)
        self.assertEqual(self.audio.read_bytes(), b'test audio')

    def test_offline_buffer_survives_unplug_and_restart(self):
        with patch('logbook.remote_agent.validate_volume', return_value=self.folder):
            result = run_agent(self.config, client=httpx.Client(base_url='http://test', transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(httpx.ConnectError('offline')))))
        self.assertEqual(result['pending_bytes'], 10)
        self.assertEqual(result['retries'], 1)
        self.audio.unlink()  # simulate unplugged source using only synthetic test data
        calls = []
        def respond(request):
            calls.append(request)
            if request.url.path.endswith('/status'):
                return httpx.Response(200, json={'ok': True})
            return httpx.Response(200, json={'state': 'present', 'receipt_id': 'recording-1'})
        with patch('logbook.remote_agent.validate_volume', side_effect=OSError('unplugged')):
            result = run_agent(self.config, client=httpx.Client(base_url='http://test', transport=httpx.MockTransport(respond)),
                               now=10**12)
        self.assertEqual(result['pending_bytes'], 0)
        self.assertTrue(any(self.config.root.glob('spool/*/audio.mp3')))
        self.assertEqual(len(calls), 2)

    def test_stable_snapshot_and_orphan_recovery(self):
        box = Outbox(self.config)
        box.snapshot(self.folder)
        row = box.pending(now=10**12)[0]
        box.db.execute('DELETE FROM outbox')
        box.db.commit()
        box.close()
        box = Outbox(self.config)
        self.addCleanup(box.close)
        self.assertEqual(box.pending(now=10**12)[0]['checksum'], row['checksum'])
        self.assertEqual(json.loads(row['metadata'])['recorded_at'], '2026-10-09T08:30:00')
        self.assertEqual(json.loads(row['metadata'])['timezone'], 'America/Los_Angeles')
        young = Outbox(replace(self.config, root=self.root / 'young', stable_seconds=600))
        self.addCleanup(young.close)
        young.snapshot(self.folder)
        self.assertEqual(young.pending(now=10**12), [])

    def test_resume_transfers_from_server_offset_and_records_receipt(self):
        captured = []
        def respond(request):
            if request.method == 'PUT':
                captured.append((request.headers['x-offset'], request.content))
                self.assertEqual(request.headers['x-chunk-sha256'], sha256(request.content).hexdigest())
                return httpx.Response(200, json={'offset': 10})
            if request.url.path.endswith('/complete'):
                return httpx.Response(200, json={'state': 'present', 'receipt_id': 'recording-7'})
            if request.url.path.endswith('/status'):
                return httpx.Response(200, json={'ok': True})
            return httpx.Response(200, json={'state': 'uploading', 'upload_id': 'abc', 'offset': 4})
        with patch('logbook.remote_agent.validate_volume', return_value=self.folder):
            result = run_agent(self.config, client=httpx.Client(base_url='http://test', transport=httpx.MockTransport(respond)))
        self.assertEqual(captured, [('4', b' audio')])
        self.assertEqual(result['pending_bytes'], 0)
        self.assertTrue(self.audio.exists())

    def test_disk_full_still_preserves_original(self):
        box = Outbox(self.config)
        self.addCleanup(box.close)
        with patch('logbook.remote_agent.shutil.disk_usage') as usage:
            usage.return_value.free = 0
            with self.assertRaises(OSError):
                box.snapshot(self.folder)
        self.assertEqual(self.audio.read_bytes(), b'test audio')
        self.assertEqual(box.pending(now=10**12), [])

    def test_permission_denial_and_busy_agent_are_redacted(self):
        import fcntl
        client = httpx.Client(base_url='http://test', transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={'ok': True})))
        with patch('logbook.remote_agent.validate_volume', side_effect=PermissionError('/private')):
            self.assertEqual(run_agent(self.config, client=client)['failure'], 'recorder_access_denied')
        with (self.config.root / 'agent.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(run_agent(self.config, client=client), {'busy': True})
        with patch('logbook.remote_agent.validate_volume', side_effect=ValueError('wrong device')):
            with patch('logbook.remote_agent.httpx.Client', return_value=client):
                run_agent(self.config)
        self.assertTrue(client.is_closed)

    def test_repeated_scans_and_corrupt_orphan_fail_safely(self):
        box = Outbox(self.config)
        self.addCleanup(box.close)
        (box.spool / '.interrupted').mkdir()
        (self.folder / 'empty.mp3').touch()
        box.snapshot(self.folder)
        box.snapshot(self.folder)
        box.recover()
        self.assertEqual(len(box.pending(10**12)), 1)
        audio = next(box.spool.glob('*/audio.mp3'))
        audio.write_bytes(b'bad')
        box.db.execute('DELETE FROM outbox')
        box.db.commit()
        with self.assertRaises(ValueError):
            box.recover()
        bad_config = replace(self.config, recordings_path='missing')
        with patch('logbook.remote_agent.subprocess.run') as run:
            run.return_value.stdout = plistlib.dumps(self.volume)
            with self.assertRaises(ValueError):
                validate_volume(bad_config)

    def test_changing_recording_never_enters_outbox(self):
        from logbook.remote_agent import _signature
        box = Outbox(self.config)
        self.addCleanup(box.close)
        actual = _signature(self.audio.stat())
        for signatures in ([('different',), actual], [actual, actual, ('different',), actual]):
            with patch('logbook.remote_agent._signature', side_effect=signatures):
                with self.assertRaises(OSError):
                    box.snapshot(self.folder)
        self.assertEqual(box.pending(10**12), [])
        self.assertEqual(list(box.spool.iterdir()), [])

    def test_http_errors_bad_receipts_and_offsets_remain_pending(self):
        from logbook.remote_agent import transfer
        box = Outbox(self.config)
        self.addCleanup(box.close)
        box.snapshot(self.folder)
        row = box.pending(10**12)[0]
        for reply in ({'state': 'present'}, {'state': 'uploading', 'offset': -1},
                      {'state': 'uploading', 'offset': 0, 'upload_id': '../escape'}):
            client = httpx.Client(base_url='http://test', transport=httpx.MockTransport(
                lambda request, reply=reply: httpx.Response(200, json=reply)))
            with self.assertRaises(ValueError):
                transfer(box, row, client)
        for status in (401, 507):
            client = httpx.Client(base_url='http://test', transport=httpx.MockTransport(
                lambda request, status=status: httpx.Response(status)))
            with patch('logbook.remote_agent.validate_volume', return_value=self.folder):
                result = run_agent(self.config, client=client, now=10**12 + status * 100)
            self.assertEqual(result['pending_bytes'], 10)
        def reply(request):
            if request.method == 'PUT':
                return httpx.Response(200, json={'offset': 999})
            return httpx.Response(200, json={'state': 'uploading', 'offset': 0, 'upload_id': 'abc'})
        client = httpx.Client(base_url='http://test', transport=httpx.MockTransport(reply))
        with self.assertRaises(ValueError):
            transfer(box, row, client)
        next(box.spool.glob('*/audio.mp3')).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            transfer(box, row, client)
