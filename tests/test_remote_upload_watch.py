import fcntl
import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest import TestCase
from unittest.mock import patch

import httpx

from logbook.remote_agent import Outbox, UploadCancelled
from logbook.remote_config import AgentConfig
from logbook.remote_controls import read_status
from logbook.remote_upload_watch import WatchUploads


class WatchUploadTests(TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        self.config = AgentConfig(root / 'buffer', root / 'SONY', 'SONY', 'uuid',
                                  'REC_FILE/FOLDER01', 'UTC', 'http://127.0.0.1:8790', root / 'token',
                                  reserve_bytes=0, stable_seconds=0)
        self.config.token_file.write_text('x' * 40)
        self.config.token_file.chmod(0o600)
        folder = root / 'fixture'
        folder.mkdir()
        (folder / '261011_1200.mp3').write_bytes(b'test audio')
        box = Outbox(self.config)
        box.snapshot(folder)
        box.close()

    def test_background_upload_without_recorder_and_live_progress(self):
        entered, release = Event(), Event()
        requests = []
        def respond(request):
            requests.append(request.url.path)
            if request.method == 'PUT':
                entered.set()
                self.assertTrue(release.wait(3))
                return httpx.Response(200, json={'offset': 10})
            if request.url.path.endswith('/complete'):
                return httpx.Response(200, json={'state': 'present', 'receipt_id': 'receipt'})
            return httpx.Response(200, json={'state': 'uploading', 'upload_id': 'abc', 'offset': 4})
        client = httpx.Client(base_url='http://test', transport=httpx.MockTransport(respond))
        with patch('logbook.remote_agent.httpx.Client', return_value=client), \
                patch('logbook.remote_agent.validate_volume') as scan:
            with WatchUploads(self.config) as worker:
                self.assertTrue(entered.wait(3))
                self.assertEqual(worker.snapshot(), {'stage': 'Uploading', 'done': 4, 'total': 10})
                with (self.config.root / 'agent.lock').open('a') as lock:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                release.set()
                deadline = time.monotonic() + 3
                while read_status(self.config)['pending'] and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(read_status(self.config)['received'], 1)
            worker.thread.join(3)
        scan.assert_not_called()
        self.assertFalse(worker.thread.is_alive())
        self.assertIn('/ingest/uploads/abc/complete', requests)
        self.assertTrue(client.is_closed)
        self.assertFalse(self.config.mount_path.exists())

    def test_busy_keeps_first_attempt_then_subsequent_pass_obeys_schedule(self):
        worker = WatchUploads(self.config)
        def finish(delay):
            if worker.stop.wait.call_count == 3:
                worker.stop.set()
        with patch('logbook.remote_upload_watch.run_agent', side_effect=[{'busy': True}, {}, {}]) as run, \
                patch.object(worker.stop, 'wait', side_effect=finish):
            worker.run()
        self.assertEqual([c.kwargs['force'] for c in run.call_args_list], [True, True, False])
        self.assertTrue(all(c.kwargs['scan'] is False for c in run.call_args_list))

    def test_missing_buffer_safe_errors_and_cancellation(self):
        worker = WatchUploads(self.config)
        with patch('logbook.remote_upload_watch.run_agent', side_effect=ValueError('/private/secret')), \
                patch.object(worker.stop, 'wait', side_effect=lambda delay: worker.stop.set()):
            worker.run()
        self.assertIn('unavailable', worker.snapshot()['stage'])
        self.assertNotIn('/private', json.dumps(worker.snapshot()))
        with self.assertRaises(UploadCancelled):
            worker.update({})
        worker = WatchUploads(self.config)
        with patch('logbook.remote_upload_watch.run_agent', side_effect=UploadCancelled):
            worker.run()
        self.config.root.joinpath('outbox.sqlite').rename(self.config.root / 'saved.sqlite')
        worker = WatchUploads(self.config)
        with patch.object(worker.stop, 'wait', side_effect=lambda delay: worker.stop.set()):
            worker.run()
        self.assertEqual(worker.snapshot()['stage'], 'No recordings buffered yet')
        self.assertFalse(self.config.root.joinpath('outbox.sqlite').exists())

    def test_quit_during_network_request_leaves_resumable_buffer(self):
        entered, release = Event(), Event()
        requests = []
        def respond(request):
            requests.append(request.url.path)
            entered.set()
            self.assertTrue(release.wait(3))
            return httpx.Response(200, json={'state': 'uploading', 'upload_id': 'abc', 'offset': 0})
        client = httpx.Client(base_url='http://test', transport=httpx.MockTransport(respond))
        with patch('logbook.remote_agent.httpx.Client', return_value=client):
            with WatchUploads(self.config) as worker:
                self.assertTrue(entered.wait(3))
                start = time.monotonic()
            self.assertLess(time.monotonic() - start, 1)
            release.set()
            worker.thread.join(3)
        self.assertFalse(worker.thread.is_alive())
        self.assertEqual(requests, ['/ingest/uploads'])
        self.assertEqual(read_status(self.config)['pending'], 1)
        self.assertEqual(read_status(self.config)['retries'], 0)
        self.assertTrue(next((self.config.root / 'spool').glob('*/audio.mp3')).exists())
