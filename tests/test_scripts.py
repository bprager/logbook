from __future__ import annotations

from pathlib import Path
from unittest import TestCase


class OperatorScriptTests(TestCase):
    def test_watch_script_wraps_local_watch_command(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "scripts" / "watch"

        self.assertTrue(script.exists())
        self.assertTrue(script.stat().st_mode & 0o111)
        self.assertEqual(
            script.read_text(encoding="utf-8"),
            "\n".join(
                [
                    "#!/usr/bin/env bash",
                    "set -euo pipefail",
                    "",
                    "cd /Users/bernd/Projects/Logbook",
                    "PYTHONPATH=src .venv/bin/python -m logbook.cli watch --env .env --ui curses",
                    "",
                ]
            ),
        )

    def test_eject_voice_recorder_script_is_tracked_operator_helper(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "scripts" / "eject-voice-recorder"

        self.assertTrue(script.exists())
        self.assertTrue(script.stat().st_mode & 0o111)
        self.assertIn("diskutil eject", script.read_text(encoding="utf-8"))
