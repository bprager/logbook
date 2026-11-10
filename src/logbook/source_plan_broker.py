"""Bounded plan generation by the existing ledger-owning account."""
from __future__ import annotations

import json
import os
import pwd
import sqlite3
import stat
import sys
import tempfile
from pathlib import Path

from logbook.config import load_app_config
from logbook.graph_maintenance_worker import read_private_json
from logbook.operator_plan import _backup_ledger, prepare_job_plan
from logbook.plan_producer import is_index_request, parse_index_request, parse_job_request

SOURCE_CONFIG = Path('/Library/Application Support/LogbookPlanProducer/source.env')
SOURCE_ROOT = Path('/Users/bernd/VoiceIngest')


def source_job_ids(config: object, after: int, limit: int) -> list[int]:
    with tempfile.TemporaryDirectory() as temporary:
        snapshot = Path(temporary)/'ledger.sqlite'
        _backup_ledger(config.sqlite_path,snapshot)
        with sqlite3.connect(snapshot) as connection:
            rows = connection.execute('SELECT id FROM recording_jobs WHERE id > ? ORDER BY id LIMIT ?',
                                      (after,limit)).fetchall()
    return [row[0] for row in rows]


def source_plan(config: object, raw: bytes) -> bytes:
    if is_index_request(raw):
        after,limit = parse_index_request(raw)
        return json.dumps({'job_ids':source_job_ids(config,after,limit)},sort_keys=True).encode()
    job_id = parse_job_request(raw)
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary)/'plan.json'
        prepare_job_plan(config, job_id, path)
        output = json.dumps(read_private_json(path), sort_keys=True).encode()
    if len(output) > 5000000:
        raise ValueError('source plan exceeds limit')
    return output


def trusted_config(path: Path) -> None:
    for current in (path, *path.parents):
        info = current.lstat()
        expected = stat.S_ISREG(info.st_mode) if current == path else stat.S_ISDIR(info.st_mode)
        if not expected or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('source configuration is not root controlled')


def main() -> None:  # pragma: no cover
    if os.geteuid() != pwd.getpwnam('bernd').pw_uid or len(sys.argv) != 1:
        raise ValueError('fixed source account and no arguments required')
    trusted_config(SOURCE_CONFIG)
    config = load_app_config(SOURCE_CONFIG)
    if config.processing_root != SOURCE_ROOT or config.sqlite_path != SOURCE_ROOT/'voice_ingest.sqlite':
        raise ValueError('source configuration does not match fixed ledger')
    sys.stdout.buffer.write(source_plan(config, sys.stdin.buffer.read(1025)))
