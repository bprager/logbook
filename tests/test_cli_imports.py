from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest import TestCase


class CliImportTests(TestCase):
    def test_cli_import_does_not_require_fastapi_for_non_server_commands(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(repo_root / "src")
        script = """
import builtins

real_import = builtins.__import__

def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "fastapi" or name.startswith("fastapi."):
        raise ModuleNotFoundError("No module named 'fastapi'")
    return real_import(name, globals, locals, fromlist, level)

builtins.__import__ = blocked_import
import logbook.cli
print("ok")
"""

        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "ok")
