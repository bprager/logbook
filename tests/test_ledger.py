from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from logbook.ledger import open_ledger
from logbook.recorder import discover_recordings


class LedgerTests(TestCase):
    def test_record_discovery_is_idempotent_by_checksum(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            recordings_dir = root / "recordings"
            recordings_dir.mkdir()
            recording_path = recordings_dir / "260429_0821.mp3"
            recording_path.write_bytes(b"audio")
            timestamp = datetime(2026, 4, 29, 8, 21, 54).timestamp()
            os.utime(recording_path, (timestamp, timestamp))
            candidate = discover_recordings(recordings_dir)[0]

            ledger = open_ledger(root / "voice_ingest.sqlite", initialize=True)
            try:
                first = ledger.record_discovery(candidate, "abc123", "IC RECORDER")
                second = ledger.record_discovery(candidate, "abc123", "IC RECORDER")
            finally:
                ledger.close()

            self.assertEqual(first.id, second.id)
            self.assertEqual(second.status, "discovered")

    def test_get_by_id_returns_recording_job(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            recordings_dir = root / "recordings"
            recordings_dir.mkdir()
            recording_path = recordings_dir / "260429_0821.mp3"
            recording_path.write_bytes(b"audio")
            timestamp = datetime(2026, 4, 29, 8, 21, 54).timestamp()
            os.utime(recording_path, (timestamp, timestamp))
            candidate = discover_recordings(recordings_dir)[0]

            ledger = open_ledger(root / "voice_ingest.sqlite", initialize=True)
            try:
                written = ledger.record_discovery(candidate, "abc123", "IC RECORDER")
                loaded = ledger.get_by_id(written.id)
            finally:
                ledger.close()

            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.checksum_sha256, "abc123")
