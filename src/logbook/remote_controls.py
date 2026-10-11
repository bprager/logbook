"""Local remote-agent observation and fail-closed, offline-safe recorder eject."""
from __future__ import annotations

import fcntl
import json
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from datetime import datetime

from logbook.checksum import sha256_file
from logbook.remote_agent import _signature, validate_volume


def _database(config):
    return sqlite3.connect((config.root / 'outbox.sqlite').resolve().as_uri() + '?mode=ro', uri=True)


def read_status(config):
    if not (config.root / 'outbox.sqlite').exists():
        return {'state': 'not_started'}
    with closing(_database(config)) as db:
        rows = db.execute('SELECT metadata,receipt,retries,failure FROM outbox').fetchall()
        scan = db.execute("SELECT value FROM agent_status WHERE key='scan'").fetchone()
    pending = [row for row in rows if row[1] is None]
    return {'state': 'buffered', 'buffered': len(rows), 'received': len(rows) - len(pending),
            'pending': len(pending),
            'pending_bytes': sum(json.loads(row[0])['size'] for row in pending),
            'retries': sum(row[2] for row in pending),
            'failure': next((row[3] for row in pending if row[3]), None),
            'last_scan': scan[0] if scan else None}


def watch(config, once=False, ui='curses'):
    if not once and ui == 'curses':
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ValueError('Interactive terminal required; use --once or --ui compact.')
        from logbook.remote_tui import run_watch
        return run_watch(lambda: read_status(config))
    try:
        while True:
            status = read_status(config)
            detail = ' | '.join(f'{key.replace("_", " ")}: {value}' for key, value in status.items())
            print(datetime.now().isoformat(timespec='seconds'), detail, flush=True)
            if once:
                return
            time.sleep(2)
    except KeyboardInterrupt:
        return


def _sources(folder):
    return sorted(path for path in folder.iterdir()
                  if not path.name.startswith('.') and path.suffix.lower() == '.mp3')


def eject(config):
    # Hold the same lock as the signed agent throughout verification and unmount.
    # No snapshot or receipt is created by this command, and no upload is required.
    with (config.root / 'agent.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        folder = validate_volume(config)
        sources = _sources(folder)
        signatures = {}
        with closing(_database(config)) as db:
            for source in sources:
                if source.is_symlink() or not source.is_file():
                    raise ValueError('recording is not a regular file; refusing eject')
                signatures[source] = _signature(source.stat())
                digest = sha256_file(source)
                row = db.execute('SELECT metadata FROM outbox WHERE checksum=?', (digest,)).fetchone()
                audio = config.root / 'spool' / digest / 'audio.mp3'
                if (not row or audio.is_symlink() or not audio.is_file() or
                        audio.stat().st_size != json.loads(row[0])['size'] or
                        sha256_file(audio) != digest):
                    raise ValueError('recording not safely buffered; wait for the agent and retry')
        if (_sources(folder) != sources or any(path.is_symlink() or _signature(path.stat()) != signature
                                               for path, signature in signatures.items())):
            raise ValueError('recorder changed during verification; retry')
        validate_volume(config)
        subprocess.run(['/usr/sbin/diskutil', 'eject', str(config.mount_path)], check=True, timeout=30)
        return len(sources)
