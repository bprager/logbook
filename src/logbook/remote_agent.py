"""Read-only Sony snapshotter and persistent, restartable upload outbox."""
from __future__ import annotations

import fcntl
import json
import os
import plistlib
import shutil
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from logbook.checksum import sha256_file
from logbook.recorder import parse_sony_recording_name
from logbook.remote_config import AgentConfig, read_secret
from logbook.remote_store import RecordingMetadata, fsync_directory


def validate_volume(config: AgentConfig):
    result = subprocess.run(['/usr/sbin/diskutil', 'info', '-plist', str(config.mount_path)],
                            capture_output=True, check=True, timeout=15)
    info = plistlib.loads(result.stdout)
    if (info.get('VolumeUUID') != config.volume_uuid or
            info.get('VolumeName') != config.volume_name or
            info.get('MountPoint') != str(config.mount_path) or
            info.get('Internal') is not False or info.get('BusProtocol') != 'USB'):
        raise ValueError('recorder_identity_mismatch')
    folder = config.mount_path / config.recordings_path
    if folder.resolve() != folder.absolute() or not folder.is_dir():
        raise ValueError('recorder_path_invalid')
    return folder


def _signature(stat):
    return stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


class Outbox:
    def __init__(self, config: AgentConfig):
        self.config = config
        config.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        config.root.chmod(0o700)
        fsync_directory(config.root.parent)
        self.spool = config.root / 'spool'
        self.spool.mkdir(parents=True, exist_ok=True, mode=0o700)
        fsync_directory(config.root)
        self.db = sqlite3.connect(config.root / 'outbox.sqlite')
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS outbox (
            checksum TEXT PRIMARY KEY, metadata TEXT NOT NULL, receipt TEXT,
            retries INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
            failure TEXT)''')
        self.db.execute('CREATE TABLE IF NOT EXISTS agent_status (key TEXT PRIMARY KEY, value TEXT)')
        self.db.commit()
        self.recover()

    def close(self):
        self.db.close()

    def recover(self):
        # Only published directories are eligible. Interrupted temporary snapshots
        # are never uploaded and never acknowledged; the recorder remains intact.
        for directory in self.spool.iterdir():
            if len(directory.name) != 64 or not directory.is_dir():
                continue
            if self.db.execute('SELECT 1 FROM outbox WHERE checksum=?',
                               (directory.name,)).fetchone():
                continue
            metadata = json.loads((directory / 'metadata.json').read_text())
            meta = RecordingMetadata(**metadata)
            meta.validate(self.config.max_bytes)
            audio = directory / 'audio.mp3'
            if (directory.name != meta.sha256 or audio.stat().st_size != meta.size or
                    sha256_file(audio) != meta.sha256):
                raise ValueError('spool_corruption')
            self._register(meta)

    def _register(self, meta):
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO outbox(checksum,metadata) VALUES (?,?)',
                            (meta.sha256, json.dumps(asdict(meta))))

    def snapshot(self, folder):
        for source in sorted(folder.iterdir()):
            if (source.is_symlink() or not source.is_file() or source.name.startswith('.') or
                    source.suffix.lower() != '.mp3'):
                continue
            before = source.stat()
            if not 0 < before.st_size <= self.config.max_bytes:
                continue
            if time.time() - before.st_mtime < self.config.stable_seconds:
                continue
            if shutil.disk_usage(self.spool).free < before.st_size + self.config.reserve_bytes:
                raise OSError('storage_pressure')
            staging = Path(tempfile.mkdtemp(prefix='.snapshot-', dir=self.spool))
            try:
                digest = sha256()
                count = 0
                fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd, 'rb') as reader, (staging / 'audio.mp3').open('wb') as writer:
                    if _signature(os.fstat(reader.fileno())) != _signature(before):
                        raise OSError('source_changed')
                    while chunk := reader.read(self.config.chunk_bytes):
                        count += len(chunk)
                        if count > before.st_size:
                            raise OSError('source_changed')
                        digest.update(chunk)
                        writer.write(chunk)
                    writer.flush()
                    os.fsync(writer.fileno())
                if count != before.st_size or _signature(source.stat()) != _signature(before):
                    raise OSError('source_changed')
                recorded_at, _ = parse_sony_recording_name(source.name)
                recorded_at = recorded_at or datetime.fromtimestamp(
                    before.st_mtime, ZoneInfo(self.config.timezone)).replace(tzinfo=None)
                meta = RecordingMetadata(digest.hexdigest(), count, source.name,
                                         recorded_at.isoformat(timespec='seconds'),
                                         self.config.timezone)
                meta.validate(self.config.max_bytes)
                with (staging / 'metadata.json').open('w') as manifest:
                    json.dump(asdict(meta), manifest)
                    manifest.flush()
                    os.fsync(manifest.fileno())
                fsync_directory(staging)
                target = self.spool / meta.sha256
                if not target.exists():
                    os.rename(staging, target)
                    fsync_directory(self.spool)
                self._register(meta)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)

    def pending(self, now):
        return self.db.execute('SELECT * FROM outbox WHERE receipt IS NULL AND next_attempt<=? '
                               'ORDER BY checksum', (now,)).fetchall()

    def failure(self, checksum, code, now):
        with self.db:
            self.db.execute('UPDATE outbox SET retries=retries+1, failure=?, '
                            'next_attempt=? + min(3600, 30 * (1 << min(retries,7))) '
                            'WHERE checksum=?', (code, now, checksum))

    def acknowledge(self, checksum, receipt):
        if receipt.get('state') != 'present' or not isinstance(receipt.get('receipt_id'), str):
            raise ValueError('invalid_receipt')
        with self.db:
            self.db.execute('UPDATE outbox SET receipt=?,failure=NULL WHERE checksum=?',
                            (receipt['receipt_id'], checksum))

    def status(self, failure=None):
        if failure is not None:
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO agent_status VALUES ('scan',?)", (failure,))
        rows = self.db.execute('SELECT * FROM outbox WHERE receipt IS NULL').fetchall()
        scan = self.db.execute("SELECT value FROM agent_status WHERE key='scan'").fetchone()
        return {'pending_bytes': sum(json.loads(row['metadata'])['size'] for row in rows),
                'retries': sum(row['retries'] for row in rows),
                'failure': next((row['failure'] for row in rows if row['failure']),
                                scan[0] if scan and scan[0] else None),
                'storage_pressure': shutil.disk_usage(self.spool).free < self.config.reserve_bytes}


def transfer(box, row, client):
    meta = json.loads(row['metadata'])
    # Allocation is also the reconciliation query after a lost response.
    response = client.post('/ingest/uploads', json=meta)
    response.raise_for_status()
    session = response.json()
    if session['state'] == 'present':
        box.acknowledge(row['checksum'], session)
        return
    offset = session['offset']
    if type(offset) is not int or not 0 <= offset <= meta['size']:
        raise ValueError('invalid_offset')
    upload_id = session['upload_id']
    if not isinstance(upload_id, str) or not upload_id.isalnum():
        raise ValueError('invalid_upload_id')
    audio = box.spool / row['checksum'] / 'audio.mp3'
    if audio.stat().st_size != meta['size'] or sha256_file(audio) != meta['sha256']:
        raise ValueError('spool_corruption')
    with audio.open('rb') as stream:
        stream.seek(offset)
        while chunk := stream.read(box.config.chunk_bytes):
            response = client.put('/ingest/uploads/' + upload_id, content=chunk,
                                  headers={'X-Offset': str(offset),
                                           'X-Chunk-SHA256': sha256(chunk).hexdigest()})
            response.raise_for_status()
            offset += len(chunk)
            if response.json().get('offset') != offset:
                raise ValueError('invalid_offset')
    response = client.post('/ingest/uploads/' + upload_id + '/complete')
    response.raise_for_status()
    box.acknowledge(row['checksum'], response.json())


def run_agent(config: AgentConfig, *, client=None, now=None):
    config.validate()
    config.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (config.root / 'agent.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'busy': True}
        box = Outbox(config)
        owned_client = client is None
        client = client or httpx.Client(base_url=config.server_url, timeout=30, follow_redirects=False,
                                        trust_env=False,
                                        headers={'Authorization': 'Bearer ' + read_secret(config.token_file)})
        try:
            try:
                box.snapshot(validate_volume(config))
                scan_failure = ''
            except PermissionError:
                scan_failure = 'recorder_access_denied'
            except (OSError, ValueError, subprocess.SubprocessError):
                scan_failure = 'recorder_or_storage_unavailable'
            for row in box.pending(time.time() if now is None else now):
                try:
                    transfer(box, row, client)
                except httpx.HTTPStatusError as error:
                    code = 'authentication' if error.response.status_code in (401, 403) else 'server'
                    box.failure(row['checksum'], code, time.time() if now is None else now)
                except (httpx.HTTPError, OSError, ValueError, KeyError):
                    box.failure(row['checksum'], 'transfer_unavailable',
                                time.time() if now is None else now)
            status = box.status(scan_failure)
            try:
                client.post('/ingest/status', json=status).raise_for_status()
            except httpx.HTTPError:
                pass  # persisted locally; next scheduled run reports again
            return status
        finally:
            box.close()
            if owned_client:
                client.close()
