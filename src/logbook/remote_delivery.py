"""Persistent downstream retry bookkeeping, independent of upload receipts."""
from __future__ import annotations

import time

from logbook.ledger import open_ledger, utc_now_iso
from logbook.vault_sync import FINAL_SYNC_STATUSES


def pending_remote_vault_sync(config):
    ledger = open_ledger(config.sqlite_path)
    try:
        rows = ledger.connection.execute('''SELECT j.status FROM recording_jobs j
            JOIN remote_delivery d ON d.job_id=j.id WHERE j.vault_synced_at IS NULL''')
        return any(row['status'] in FINAL_SYNC_STATUSES for row in rows)
    finally:
        ledger.close()


def pending_remote_graph_jobs(config):
    if config.memgraph is None:
        return ()
    ledger = open_ledger(config.sqlite_path)
    try:
        return tuple(row['job_id'] for row in ledger.connection.execute('''
            SELECT d.job_id FROM remote_delivery d JOIN recording_jobs j ON j.id=d.job_id
            WHERE j.vault_synced_at IS NOT NULL AND d.graph_synced_at IS NULL
            AND d.next_attempt<=? ORDER BY d.job_id LIMIT 10''', (time.time(),)))
    finally:
        ledger.close()


def record_graph_attempt(config, job_id, succeeded):
    if not config.sqlite_path.exists():
        return  # standalone graph diagnostics need not create an ingestion ledger
    ledger = open_ledger(config.sqlite_path)
    try:
        with ledger.connection:
            ledger.connection.execute('''UPDATE remote_delivery SET attempts=attempts+1,
                graph_synced_at=?, next_attempt=? + min(3600,30 * (1 << min(attempts,7))),
                failure=? WHERE job_id=?''',
                (utc_now_iso() if succeeded else None, time.time(),
                 None if succeeded else 'graph_sync_failed', job_id))
    finally:
        ledger.close()
