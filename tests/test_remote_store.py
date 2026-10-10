from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from logbook.ledger import open_ledger
from logbook.remote_store import RemoteStore, RecordingMetadata, IngestError


class RemoteStoreTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / 'ledger.sqlite'
        ledger = open_ledger(self.db, initialize=True)
        ledger.close()
        self.store = RemoteStore(self.db, self.root / 'remote', reserve_bytes=0)
        self.data = b'synthetic recording'
        self.meta = RecordingMetadata(sha256(self.data).hexdigest(), len(self.data),
                                      '261009_0830.mp3', '2026-10-09T08:30:00',
                                      'America/Los_Angeles')

    def upload(self, device='air-one'):
        result = self.store.allocate(device, self.meta)
        if result['state'] == 'present':
            return result
        self.store.append(device, result['upload_id'], 0, self.data, self.meta.sha256)
        return self.store.complete(device, result['upload_id'])

    def test_durable_receipt_and_repeat_after_audio_pruned(self):
        receipt = self.upload()
        ledger = open_ledger(self.db)
        job = ledger.get_by_checksum(self.meta.sha256)
        self.assertEqual(job.status, 'copied')
        self.assertEqual(job.parsed_recorded_at, self.meta.recorded_at)
        Path(job.copied_path).unlink()
        ledger.close()
        self.assertEqual(self.store.check('air-two', self.meta), receipt)
        self.assertEqual(self.store.allocate('air-two', self.meta), receipt)

    def test_concurrent_clients_register_one_job(self):
        with ThreadPoolExecutor(2) as pool:
            receipts = list(pool.map(self.upload, ['air-one', 'air-two']))
        self.assertEqual(receipts[0], receipts[1])
        ledger = open_ledger(self.db)
        self.assertEqual(ledger.connection.execute('SELECT count(*) FROM recording_jobs').fetchone()[0], 1)
        ledger.close()

    def test_offsets_corruption_and_restart(self):
        session = self.store.allocate('air-one', self.meta)['upload_id']
        with self.assertRaises(IngestError):
            self.store.append('air-one', session, 1, self.data, self.meta.sha256)
        with self.assertRaises(IngestError):
            self.store.append('air-one', session, 0, self.data, '0' * 64)
        self.store.append('air-one', session, 0, self.data[:3], sha256(self.data[:3]).hexdigest())
        restarted = RemoteStore(self.db, self.root / 'remote', reserve_bytes=0)
        self.assertEqual(restarted.allocate('air-one', self.meta)['offset'], 3)
        with self.assertRaises(IngestError):
            restarted.complete('air-one', session)
        restarted.append('air-one', session, 3, self.data[3:], sha256(self.data[3:]).hexdigest())
        receipt = restarted.complete('air-one', session)
        self.assertEqual(restarted.complete('air-one', session), receipt)
        with self.assertRaises(IngestError):
            restarted.complete('air-two', session)

    def test_recover_rename_before_ledger_commit(self):
        session = self.store.allocate('air-one', self.meta)['upload_id']
        self.store.append('air-one', session, 0, self.data, self.meta.sha256)
        with patch.object(self.store, '_register', side_effect=OSError('crash')):
            with self.assertRaises(OSError):
                self.store.complete('air-one', session)
        self.assertEqual(self.store.complete('air-one', session)['state'], 'present')

    def test_invalid_metadata_and_quota(self):
        for meta in [replace(self.meta, filename='../private.mp3'),
                     replace(self.meta, sha256='x'), replace(self.meta, size=0),
                     replace(self.meta, recorded_at='bad'),
                     replace(self.meta, timezone='not-a-zone')]:
            with self.subTest(meta=meta), self.assertRaises(IngestError):
                self.store.allocate('air-one', meta)
        with patch('logbook.remote_store.shutil.disk_usage') as usage:
            usage.return_value.free = 0
            with self.assertRaises(IngestError):
                self.store.allocate('air-one', self.meta)

    def test_discovered_job_is_upgraded_without_duplicate_and_migration_repeats(self):
        from datetime import datetime
        from logbook.recorder import RecordingCandidate
        ledger = open_ledger(self.db, initialize=True)
        job = ledger.record_discovery(RecordingCandidate(Path('/synthetic/261009_0830.mp3'),
                                      self.meta.filename, self.meta.size, datetime(2026, 10, 9),
                                      datetime(2026, 10, 9, 8, 30), True, None),
                                      self.meta.sha256, 'IC RECORDER')
        ledger.initialize()
        ledger.close()
        self.assertEqual(self.upload()['receipt_id'], f'recording-{job.id}')
        with self.assertRaises(IngestError):
            self.store.check('air-one', replace(self.meta, size=999))

    def test_session_conflict_missing_bytes_and_full_digest(self):
        session = self.store.allocate('air-one', self.meta)['upload_id']
        self.assertEqual(self.store.check('air-one', self.meta)['upload_id'], session)
        with self.assertRaises(IngestError):
            self.store.allocate('air-one', replace(self.meta, size=99))
        for data in (b'', b'x' * (self.store.chunk_bytes + 1)):
            with self.assertRaises(IngestError):
                self.store.append('air-one', session, 0, data, sha256(data).hexdigest())
        with patch('logbook.remote_store.shutil.disk_usage') as usage:
            usage.return_value.free = 0
            with self.assertRaises(IngestError):
                self.store.append('air-one', session, 0, self.data, self.meta.sha256)
        corrupt = b'x' * len(self.data)
        self.store.append('air-one', session, 0, corrupt, sha256(corrupt).hexdigest())
        with self.assertRaises(IngestError):
            self.store.complete('air-one', session)
        (self.store.parts / session).unlink()
        with self.assertRaises(IngestError):
            self.store.complete('air-one', session)

    def test_uncommitted_tail_is_discarded_and_missing_prefix_rejected(self):
        session = self.store.allocate('air-one', self.meta)['upload_id']
        part = self.store.parts / session
        part.write_bytes(b'uncommitted tail')
        self.store.append('air-one', session, 0, self.data[:3], sha256(self.data[:3]).hexdigest())
        self.assertEqual(part.read_bytes(), self.data[:3])
        part.write_bytes(b'')
        with self.assertRaises(IngestError):
            self.store.append('air-one', session, 3, self.data[3:], sha256(self.data[3:]).hexdigest())
        for timestamp in ('2026-10-09T08:30:00+00:00', '1900-01-01T00:00:00'):
            with self.assertRaises(IngestError):
                self.store.allocate('air-one', replace(self.meta, recorded_at=timestamp))
