"""Read-only, allowlisted remote telemetry for the existing observer surfaces."""
from __future__ import annotations

import sqlite3
from pathlib import Path


FAILURE_CODES = {None, '', 'recorder_access_denied', 'recorder_or_storage_unavailable',
                 'transfer_unavailable', 'authentication', 'server'}


def read_remote_status(path: Path):
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
        try:
            devices = [dict(row) for row in connection.execute(
                'SELECT device,last_seen,pending_bytes,retries,failure,storage_pressure '
                'FROM remote_devices ORDER BY device LIMIT 100')]
            uploads = dict(connection.execute('''SELECT count(*) AS pending_uploads,
                coalesce(sum(size-offset),0) AS pending_bytes FROM remote_uploads u
                WHERE receipt IS NULL AND NOT EXISTS (
                    SELECT 1 FROM recording_jobs j WHERE j.checksum_sha256=u.checksum
                    AND j.status != 'discovered')''').fetchone())
            audit = [dict(row) for row in connection.execute(
                'SELECT code,count,last_seen FROM remote_audit ORDER BY code')]
            delivery = dict(connection.execute('''SELECT count(*) AS pending_graph_jobs,
                coalesce(sum(attempts),0) AS graph_attempts FROM remote_delivery
                WHERE graph_synced_at IS NULL''').fetchone())
            return {'state': 'available', 'devices': devices, 'uploads': uploads,
                    'delivery': delivery, 'audit': audit}
        finally:
            connection.close()
    except sqlite3.Error:
        return {'state': 'unavailable', 'devices': [], 'uploads': {}, 'audit': []}
