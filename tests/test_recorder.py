from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from unittest import TestCase

from logbook.config import RecorderConfig
from logbook.recorder import discover_recordings, parse_sony_recording_name, validate_recorder


class RecorderTests(TestCase):
    def test_parse_sony_recording_name(self) -> None:
        parsed_at, sequence = parse_sony_recording_name("260427_0837_01.mp3")

        self.assertEqual(parsed_at, datetime(2026, 4, 27, 8, 37))
        self.assertEqual(sequence, "01")

    def test_discover_recordings_ignores_sidecars_and_non_mp3(self) -> None:
        root = Path(self._testMethodName)
        recordings_dir = root / "IC RECORDER" / "REC_FILE" / "FOLDER01"
        recordings_dir.mkdir(parents=True)
        self.addCleanup(_cleanup_tree, root)
        mp3 = recordings_dir / "260429_0821.mp3"
        mp3.write_bytes(b"audio")
        os.utime(mp3, (datetime(2026, 4, 29, 8, 21, 54).timestamp(),) * 2)
        (recordings_dir / "._260429_0821.mp3").write_bytes(b"sidecar")
        (recordings_dir / "README.TXT").write_text("ignore", encoding="utf-8")

        recordings = discover_recordings(recordings_dir)

        self.assertEqual([recording.filename for recording in recordings], ["260429_0821.mp3"])
        self.assertEqual(recordings[0].parsed_recorded_at, datetime(2026, 4, 29, 8, 21))
        self.assertIs(recordings[0].timestamp_matches_mtime, True)

    def test_validate_recorder_accepts_operational_mount(self) -> None:
        root = Path(self._testMethodName)
        mount = root / "IC RECORDER"
        recordings_dir = mount / "REC_FILE" / "FOLDER01"
        recordings_dir.mkdir(parents=True)
        self.addCleanup(_cleanup_tree, root)

        validation = validate_recorder(
            RecorderConfig(
                volume_name="IC RECORDER",
                mount_path=mount,
                recordings_path="/REC_FILE/FOLDER01",
            )
        )

        self.assertIs(validation.operational, True)
        self.assertEqual(validation.recordings_dir, recordings_dir)
        self.assertEqual(validation.warnings, ())


def _cleanup_tree(path: Path) -> None:
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_file():
            child.unlink()
        elif child.is_dir():
            child.rmdir()
    if path.exists():
        path.rmdir()
