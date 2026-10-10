"""Durable, resumable intake. The existing job hash remains the authority."""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from logbook.checksum import sha256_file
from logbook.ledger import utc_now_iso


class IngestError(ValueError):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code, self.status = code, status


@dataclass(frozen=True)
class RecordingMetadata:
    sha256: str
    size: int
    filename: str
    recorded_at: str
    timezone: str

    def validate(self, max_bytes: int):
        if not re.fullmatch(r'[0-9a-f]{64}', self.sha256):
            raise IngestError('invalid_hash')
        if type(self.size) is not int or not 0 < self.size <= max_bytes:
            raise IngestError('invalid_size', 413)
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}\.[mM][pP]3', self.filename):
            raise IngestError('invalid_filename')
        try:
            timestamp = datetime.fromisoformat(self.recorded_at)
            if timestamp.tzinfo is not None or not 2000 <= timestamp.year <= 2100:
                raise ValueError('invalid recorder wall clock')
            ZoneInfo(self.timezone)
        except (ValueError, ZoneInfoNotFoundError):
            raise IngestError('invalid_timestamp_or_timezone') from None


def migrate_remote(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS remote_uploads (
        id TEXT PRIMARY KEY, device TEXT NOT NULL, checksum TEXT NOT NULL,
        size INTEGER NOT NULL, metadata TEXT NOT NULL, offset INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, receipt TEXT,
        UNIQUE(device, checksum))''')
    connection.execute('''CREATE TABLE IF NOT EXISTS remote_devices (
        device TEXT PRIMARY KEY, last_seen TEXT NOT NULL, requests INTEGER NOT NULL DEFAULT 0,
        window INTEGER NOT NULL DEFAULT 0, window_requests INTEGER NOT NULL DEFAULT 0,
        pending_bytes INTEGER NOT NULL DEFAULT 0, retries INTEGER NOT NULL DEFAULT 0,
        failure TEXT, storage_pressure INTEGER NOT NULL DEFAULT 0)''')
    connection.execute('''CREATE TABLE IF NOT EXISTS remote_delivery (
        job_id INTEGER PRIMARY KEY, graph_synced_at TEXT, attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt REAL NOT NULL DEFAULT 0, failure TEXT)''')
    connection.execute('''CREATE TABLE IF NOT EXISTS recorder_associations (
        checksum TEXT PRIMARY KEY, source_path TEXT NOT NULL, volume_uuid TEXT NOT NULL,
        observed_at TEXT NOT NULL)''')
    connection.execute('''CREATE TABLE IF NOT EXISTS remote_audit (
        code TEXT PRIMARY KEY, count INTEGER NOT NULL, last_seen TEXT NOT NULL)''')


def fsync_directory(path: Path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class RemoteStore:
    def __init__(self, ledger_path: Path, root: Path, *, max_bytes=2 * 1024**3,
                 quota_bytes=20 * 1024**3, reserve_bytes=1024**3, chunk_bytes=1024**2, max_active_uploads=128):
        self.ledger_path, self.root = ledger_path, root
        self.max_bytes, self.quota_bytes = max_bytes, quota_bytes
        self.reserve_bytes, self.chunk_bytes = reserve_bytes, chunk_bytes
        self.max_active_uploads = max_active_uploads
        self.parts, self.inbox = root / 'partial', root / 'inbox'
        for directory in (root, self.parts, self.inbox):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)
            fsync_directory(directory.parent)
        fsync_directory(root)

    @contextmanager
    def transaction(self):
        connection = sqlite3.connect(self.ledger_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('PRAGMA synchronous=FULL')
            connection.execute('BEGIN IMMEDIATE')
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _present(self, db, meta):
        job = db.execute('SELECT id, status, size_bytes FROM recording_jobs WHERE checksum_sha256=?',
                         (meta.sha256,)).fetchone()
        if job and job['size_bytes'] != meta.size:
            raise IngestError('size_conflict', 409)
        if job and job['status'] != 'discovered':
            return {'state': 'present', 'receipt_id': f"recording-{job['id']}"}
        return None

    def check(self, device: str, meta: RecordingMetadata):
        meta.validate(self.max_bytes)
        with self.transaction() as db:
            present = self._present(db, meta)
            if present:
                self._settle(db, meta.sha256, present)
                return present
            row = db.execute('SELECT * FROM remote_uploads WHERE device=? AND checksum=?',
                             (device, meta.sha256)).fetchone()
            return self._session(row) if row else {'state': 'missing'}

    def allocate(self, device: str, meta: RecordingMetadata):
        meta.validate(self.max_bytes)
        with self.transaction() as db:
            present = self._present(db, meta)
            if present:
                self._settle(db, meta.sha256, present)
                return present
            row = db.execute('SELECT * FROM remote_uploads WHERE device=? AND checksum=?',
                             (device, meta.sha256)).fetchone()
            if row:
                if row['size'] != meta.size:
                    raise IngestError('size_conflict', 409)
                return self._session(row)
            active = db.execute('SELECT count(*) FROM remote_uploads WHERE device=? '
                                'AND receipt IS NULL', (device,)).fetchone()[0]
            if active >= self.max_active_uploads:
                raise IngestError('too_many_uploads', 429)
            reserved = db.execute('SELECT coalesce(sum(size-offset),0) FROM remote_uploads '
                                  'WHERE receipt IS NULL').fetchone()[0]
            used = sum(p.stat().st_size for folder in (self.parts, self.inbox)
                       for p in folder.iterdir())
            if (used + reserved + meta.size > self.quota_bytes or
                    shutil.disk_usage(self.root).free < reserved + meta.size + self.reserve_bytes):
                raise IngestError('storage_pressure', 507)
            upload_id = uuid.uuid4().hex
            now = utc_now_iso()
            db.execute('INSERT INTO remote_uploads '
                       '(id,device,checksum,size,metadata,created_at,updated_at) VALUES (?,?,?,?,?,?,?)',
                       (upload_id, device, meta.sha256, meta.size, json.dumps(asdict(meta)), now, now))
            return {'state': 'uploading', 'upload_id': upload_id, 'offset': 0}

    def _session(self, row):
        return {'state': 'uploading', 'upload_id': row['id'], 'offset': row['offset']}

    def _owned(self, db, device, upload_id):
        row = db.execute('SELECT * FROM remote_uploads WHERE id=? AND device=?',
                         (upload_id, device)).fetchone()
        if row is None:
            raise IngestError('unknown_upload', 404)
        return row

    def append(self, device, upload_id, offset, data, digest):
        from hashlib import sha256
        if not data or len(data) > self.chunk_bytes:
            raise IngestError('invalid_chunk_size', 413)
        if sha256(data).hexdigest() != digest:
            raise IngestError('chunk_hash_mismatch', 422)
        with self.transaction() as db:
            row = self._owned(db, device, upload_id)
            if row['receipt'] or offset != row['offset'] or offset + len(data) > row['size']:
                raise IngestError('offset_conflict', 409)
            part = self.parts / row['id']
            if shutil.disk_usage(self.root).free < len(data) + self.reserve_bytes:
                raise IngestError('storage_pressure', 507)
            with part.open('r+b' if part.exists() else 'w+b') as stream:
                # Ignore an unacknowledged tail left by a crash before the DB commit.
                if stream.seek(0, os.SEEK_END) < offset:
                    raise IngestError('partial_storage_lost', 409)
                stream.truncate(offset)
                stream.seek(offset)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            fsync_directory(self.parts)
            db.execute('UPDATE remote_uploads SET offset=?, updated_at=? WHERE id=?',
                       (offset + len(data), utc_now_iso(), upload_id))
            return {'offset': offset + len(data)}

    def complete(self, device, upload_id):
        with self.transaction() as db:
            row = self._owned(db, device, upload_id)
            meta = RecordingMetadata(**json.loads(row['metadata']))
            present = self._present(db, meta)
            if present:
                self._settle(db, meta.sha256, present)
                return present
            if row['offset'] != row['size']:
                raise IngestError('incomplete_upload', 409)
            target = self.inbox / f'{meta.sha256}.mp3'
            part = self.parts / row['id']
            source = part if part.exists() else target
            if not source.exists() or source.stat().st_size != meta.size:
                raise IngestError('incomplete_storage', 409)
            if sha256_file(source) != meta.sha256:
                # Persist a restartable offset even though this completion is rejected.
                # No job was registered and no canonical recording was acknowledged.
                db.execute('UPDATE remote_uploads SET offset=0 WHERE id=?', (upload_id,))
                db.commit()
                raise IngestError('recording_hash_mismatch', 422)
            # Repeating after a rename-before-commit crash uses the verified target.
            if source == part:
                os.replace(part, target)
            fsync_directory(self.inbox)
            fsync_directory(self.parts)
            receipt = self._register(db, meta, target, device)
            self._settle(db, meta.sha256, receipt)
            return receipt

    def _register(self, db, meta, target, device):
        now = utc_now_iso()
        db.execute('''INSERT INTO recording_jobs
            (checksum_sha256, source_device, source_filename, source_path, size_bytes,
             modified_at, parsed_recorded_at, status, first_seen_at, last_seen_at,
             copied_path, copied_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'copied', ?, ?, ?, ?)
            ON CONFLICT(checksum_sha256) DO NOTHING''',
                   (meta.sha256, 'remote:' + device, meta.filename, 'remote:' + meta.sha256,
                    meta.size, meta.recorded_at, meta.recorded_at, now, now, str(target), now))
        # Discovery alone is not a durable copy; finish that existing job in-place.
        db.execute("UPDATE recording_jobs SET status='copied', copied_path=?, copied_at=? "
                   "WHERE checksum_sha256=? AND status='discovered'",
                   (str(target), now, meta.sha256))
        job_id = db.execute('SELECT id FROM recording_jobs WHERE checksum_sha256=?',
                            (meta.sha256,)).fetchone()[0]
        db.execute('INSERT OR IGNORE INTO remote_delivery(job_id) VALUES (?)', (job_id,))
        return self._present(db, meta)

    def _settle(self, db, checksum, receipt):
        # This is the durability boundary: commit the canonical job and all
        # receipts BEFORE reclaiming redundant transfer parts. A crash while
        # reclaiming can waste space but cannot strand a competing device at an
        # acknowledged offset whose bytes no longer exist.
        db.execute('UPDATE remote_uploads SET receipt=?, updated_at=? WHERE checksum=?',
                   (receipt['receipt_id'], utc_now_iso(), checksum))
        db.commit()
        for row in db.execute('SELECT id FROM remote_uploads WHERE checksum=?', (checksum,)):
            part = self.parts / row['id']
            if part.exists():
                part.unlink()
        fsync_directory(self.parts)
