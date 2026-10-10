from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from logbook.cli import main
from logbook.config import load_app_config
from logbook.copying import copy_discovered_recordings
from logbook.ledger import open_ledger
from logbook.odin import FakeOdinClient
from logbook.remote_store import RecordingMetadata, RemoteStore
from logbook.retention import execute_audio_cleanup, plan_audio_cleanup
from test_process_mounted_recorder import _write_env, _FilesystemWriterFactory


class RemotePipelineTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.env = _write_env(self.root)
        self.config = load_app_config(self.env)
        self.config = replace(self.config, recorder=replace(self.config.recorder, volume_uuid='sony'))
        ledger = open_ledger(self.config.sqlite_path, initialize=True)
        ledger.close()
        self.store = RemoteStore(self.config.sqlite_path, self.root / 'remote', reserve_bytes=0)

    def deliver(self, filename='261008_1200.mp3', data=b'fake log recording'):
        meta = RecordingMetadata(sha256(data).hexdigest(), len(data), filename,
                                 '2026-10-08T12:00:00', 'America/Los_Angeles')
        session = self.store.allocate('air-one', meta)
        self.store.append('air-one', session['upload_id'], 0, data, meta.sha256)
        self.store.complete('air-one', session['upload_id'])
        return meta

    def process(self):
        with patch('logbook.cli.HttpOdinClient', FakeOdinClient), \
                patch('logbook.cli.ObsidianCliNoteWriter', _FilesystemWriterFactory), \
                patch('logbook.cli._mark_vault_synced_and_sync_memory', return_value=True), \
                patch('logbook.cli.copy_discovered_recordings_with_retries',
                      side_effect=AssertionError('must not access recorder')):
            return main(['process-queued', '--env', str(self.env)])

    def test_remote_processing_without_mount_and_late_arrival(self):
        self.deliver()
        self.assertEqual(self.process(), 0)
        logs = list((self.root / 'vault' / '06 - Timestamps').rglob('*-Log.md'))
        self.assertEqual(len(logs), 1)
        self.deliver(filename='261008_1000.mp3', data=b'late recording')
        self.assertEqual(self.process(), 0)
        self.assertEqual(len(list((self.root / 'vault' / '06 - Timestamps').rglob('*-Log.md'))), 1)
        self.assertIn('entry_count: "2"', logs[0].read_text())
        self.assertEqual(self.process(), 0)

    def test_remote_receipt_cannot_prune_then_exact_mimir_attachment_can(self):
        meta = self.deliver()
        folder = self.config.recorder.recordings_dir
        folder.mkdir(parents=True)
        source = folder / meta.filename
        source.write_bytes(b'fake log recording')
        self.assertEqual(plan_audio_cleanup(self.config).eligible_count, 0)
        execute_audio_cleanup(self.config, include_recorder=True)
        self.assertTrue(source.exists())
        with patch('logbook.copying.validate_remote_recorder', return_value=folder):
            copy_discovered_recordings(self.config)
        self.assertEqual(self.process(), 0)
        ledger = open_ledger(self.config.sqlite_path)
        now = datetime.now(timezone.utc)
        ledger.mark_vault_synced(meta.sha256, now.isoformat())
        job = ledger.get_by_checksum(meta.sha256)
        self.assertEqual(job.source_path, str(source))
        self.assertEqual(ledger.connection.execute('SELECT count(*) FROM recorder_associations').fetchone()[0], 1)
        ledger.close()
        with patch('logbook.retention.validate_remote_recorder', side_effect=ValueError('wrong volume')):
            execute_audio_cleanup(self.config, include_recorder=True, now=now + timedelta(days=8))
        self.assertTrue(source.exists())
        with patch('logbook.retention.validate_remote_recorder', return_value=folder):
            execute_audio_cleanup(self.config, include_recorder=True, now=now + timedelta(days=8))
        self.assertFalse(source.exists())

    def test_known_pruned_recording_is_never_requeued(self):
        meta = self.deliver()
        self.assertEqual(self.process(), 0)
        ledger = open_ledger(self.config.sqlite_path)
        job = ledger.get_by_checksum(meta.sha256)
        Path(job.copied_path).unlink()
        ledger.close()
        folder = self.config.recorder.recordings_dir
        folder.mkdir(parents=True)
        (folder / meta.filename).write_bytes(b'fake log recording')
        with patch('logbook.copying.validate_remote_recorder', return_value=folder):
            copied = copy_discovered_recordings(self.config)
        self.assertEqual(copied.copied_count, 0)
        ledger = open_ledger(self.config.sqlite_path)
        self.assertEqual(ledger.get_by_checksum(meta.sha256).status, job.status)
        ledger.close()

    def test_missing_enrollment_wrong_volume_and_pipeline_lock_fail_closed(self):
        import fcntl
        import plistlib
        from logbook.recorder import validate_remote_recorder
        config = self.config.recorder
        with self.assertRaises(ValueError):
            validate_remote_recorder(replace(config, volume_uuid=None))
        folder = config.recordings_dir
        folder.mkdir(parents=True)
        info = {'VolumeUUID': 'sony', 'VolumeName': config.volume_name,
                'MountPoint': str(config.mount_path), 'Internal': False, 'BusProtocol': 'USB'}
        with patch('logbook.recorder.subprocess.run') as run:
            run.return_value.stdout = plistlib.dumps(info)
            self.assertEqual(validate_remote_recorder(config), folder)
            run.return_value.stdout = plistlib.dumps({**info, 'VolumeUUID': 'wrong'})
            with self.assertRaises(ValueError):
                validate_remote_recorder(config)
            run.return_value.stdout = plistlib.dumps(info)
            with self.assertRaises(ValueError):
                validate_remote_recorder(replace(config, recordings_path='missing'))
        with (self.config.processing_root / 'pipeline.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.process(), 0)
        meta = self.deliver()
        (folder / meta.filename).write_bytes(b'fake log recording')
        with patch('logbook.copying.validate_remote_recorder', side_effect=ValueError('wrong')):
            copy_discovered_recordings(self.config)
        ledger = open_ledger(self.config.sqlite_path)
        self.assertTrue(ledger.get_by_checksum(meta.sha256).source_path.startswith('remote:'))
        ledger.close()
        with patch('logbook.copying.validate_remote_recorder', return_value=folder / 'wrong'):
            copy_discovered_recordings(self.config)

    def test_association_tampering_blocks_remote_pruning(self):
        meta = self.deliver()
        folder = self.config.recorder.recordings_dir
        folder.mkdir(parents=True)
        source = folder / meta.filename
        source.write_bytes(b'fake log recording')
        with patch('logbook.copying.validate_remote_recorder', return_value=folder):
            copy_discovered_recordings(self.config)
        self.assertEqual(self.process(), 0)
        ledger = open_ledger(self.config.sqlite_path)
        now = datetime.now(timezone.utc)
        ledger.mark_vault_synced(meta.sha256, now.isoformat())
        ledger.connection.execute("UPDATE recorder_associations SET volume_uuid='wrong'")
        ledger.connection.commit()
        ledger.close()
        with patch('logbook.retention.validate_remote_recorder', return_value=folder):
            execute_audio_cleanup(self.config, include_recorder=True, now=now + timedelta(days=8))
        self.assertTrue(source.exists())
