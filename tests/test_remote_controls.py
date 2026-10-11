import contextlib
import fcntl
import io
import json
import sqlite3
import subprocess
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from logbook.remote_agent import Outbox
from logbook.remote_config import AgentConfig
from logbook import remote_controls


class RemoteControlsTests(TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        self.folder = root / 'SONY' / 'REC_FILE' / 'FOLDER01'
        self.folder.mkdir(parents=True)
        self.config = AgentConfig(root / 'buffer', root / 'SONY', 'SONY', 'uuid',
                                  'REC_FILE/FOLDER01', 'UTC', 'http://127.0.0.1:8790', root / 'token',
                                  reserve_bytes=0, stable_seconds=0)
        self.audio = self.folder / '261010_1221.mp3'
        self.audio.write_bytes(b'audio')
        self.digest = sha256(b'audio').hexdigest()
        self.volume = patch('logbook.remote_controls.validate_volume', return_value=self.folder)
        self.volume.start()
        self.addCleanup(self.volume.stop)

    def snapshot(self):
        box = Outbox(self.config)
        box.snapshot(self.folder)
        box.close()

    def test_watch_is_read_only_and_reports_offline_receipts(self):
        self.assertEqual(remote_controls.read_status(self.config)['state'], 'not_started')
        self.assertFalse(self.config.root.exists())
        self.snapshot()
        box = Outbox(self.config)
        box.failure(self.digest, 'transfer_unavailable', 1)
        box.close()
        state = remote_controls.read_status(self.config)
        self.assertEqual((state['buffered'], state['pending'], state['pending_bytes']), (1, 1, 5))
        self.assertEqual(state['failure'], 'transfer_unavailable')
        self.assertNotIn('token', json.dumps(state))
        box = Outbox(self.config)
        box.acknowledge(self.digest, {'state': 'present', 'receipt_id': 'r1'})
        box.close()
        self.assertEqual(remote_controls.read_status(self.config)['received'], 1)

    def test_eject_refuses_missing_snapshot_and_never_runs_diskutil(self):
        with patch('logbook.remote_controls.subprocess.run') as run:
            with self.assertRaises((ValueError, sqlite3.Error, OSError)):
                remote_controls.eject(self.config)
            run.assert_not_called()
        self.snapshot()
        self.audio.write_bytes(b'changed')
        with patch('logbook.remote_controls.subprocess.run') as run:
            with self.assertRaisesRegex(ValueError, 'not safely buffered'):
                remote_controls.eject(self.config)
            run.assert_not_called()

    def test_offline_eject_verifies_bytes_and_preserves_audio(self):
        self.snapshot()
        (self.folder / '._hidden.mp3').write_bytes(b'sidecar')
        (self.folder / 'readme.txt').write_text('ignored')
        with patch('logbook.remote_controls.subprocess.run') as run:
            self.assertEqual(remote_controls.eject(self.config), 1)
            run.assert_called_once_with(['/usr/sbin/diskutil', 'eject', str(self.config.mount_path)],
                                        check=True, timeout=30)
        self.assertEqual(self.audio.read_bytes(), b'audio')
        self.assertEqual(remote_controls.read_status(self.config)['pending'], 1)
        (self.config.root / 'spool' / self.digest / 'audio.mp3').write_bytes(b'wrong')
        with patch('logbook.remote_controls.subprocess.run') as run:
            with self.assertRaisesRegex(ValueError, 'not safely buffered'):
                remote_controls.eject(self.config)
            run.assert_not_called()

    def test_eject_refuses_busy_agent_and_wrong_volume(self):
        self.snapshot()
        with (self.config.root / 'agent.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                remote_controls.eject(self.config)
        with patch('logbook.remote_controls.validate_volume', side_effect=ValueError('wrong volume')):
            with self.assertRaisesRegex(ValueError, 'wrong volume'):
                remote_controls.eject(self.config)

    def test_eject_refuses_symlinks_and_changes_during_verification(self):
        self.snapshot()
        link = self.folder / 'linked.mp3'
        link.symlink_to(self.audio)
        with self.assertRaisesRegex(ValueError, 'not a regular file'):
            remote_controls.eject(self.config)
        link.unlink()
        original = remote_controls.sha256_file
        def changing_hash(path):
            digest = original(path)
            if path != self.audio:
                self.audio.write_bytes(b'changed during verification')
            return digest
        with patch('logbook.remote_controls.sha256_file', side_effect=changing_hash), \
                patch('logbook.remote_controls.subprocess.run') as run:
            with self.assertRaisesRegex(ValueError, 'changed during verification'):
                remote_controls.eject(self.config)
            run.assert_not_called()

    def test_status_carries_scan_and_database_is_read_only(self):
        self.snapshot()
        box = Outbox(self.config)
        box.status('recorder_access_denied')
        box.close()
        self.assertEqual(remote_controls.read_status(self.config)['last_scan'], 'recorder_access_denied')
        with contextlib.closing(remote_controls._database(self.config)) as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute('DELETE FROM outbox')

    def test_watch_once_loop_interrupt_and_cli_errors(self):
        from logbook.remote_cli import main
        output = io.StringIO()
        with patch('logbook.remote_cli.load_remote_config', return_value=self.config), \
                contextlib.redirect_stdout(output):
            self.assertEqual(main(['watch', '--config', 'config', '--once']), 0)
            self.assertIn('not_started', output.getvalue())
            with patch('logbook.remote_controls.time.sleep', side_effect=KeyboardInterrupt):
                self.assertEqual(main(['watch', '--config', 'config', '--ui', 'compact']), 0)
            with patch('logbook.remote_controls.sys.stdin.isatty', return_value=True), \
                    patch('logbook.remote_controls.sys.stdout.isatty', return_value=True), \
                    patch('logbook.remote_tui.run_watch') as tui:
                self.assertEqual(main(['watch', '--config', 'config']), 0)
                self.assertEqual(tui.call_args.args[0](), {'state': 'not_started'})
            with patch('logbook.remote_controls.sys.stdin.isatty', return_value=False), \
                    contextlib.redirect_stderr(output):
                self.assertEqual(main(['watch', '--config', 'config']), 1)
            with patch('logbook.remote_controls.sys.stdin.isatty', return_value=True), \
                    patch('logbook.remote_controls.sys.stdout.isatty', return_value=False), \
                    contextlib.redirect_stderr(output):
                self.assertEqual(main(['watch', '--config', 'config']), 1)
            with patch('logbook.remote_controls.eject', return_value=1):
                self.assertEqual(main(['eject', '--config', 'config']), 0)
            with patch('logbook.remote_controls.eject', side_effect=ValueError('not safely buffered')), \
                    contextlib.redirect_stderr(output):
                self.assertEqual(main(['eject', '--config', 'config']), 1)
            with patch('logbook.remote_controls.eject', side_effect=subprocess.TimeoutExpired('diskutil', 30)), \
                    contextlib.redirect_stderr(output):
                self.assertEqual(main(['eject', '--config', 'config']), 1)
