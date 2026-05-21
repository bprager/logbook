from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from logbook.config import ObsidianConfig
from logbook.writers import ObsidianCliNoteWriter


class WriterTests(TestCase):
    def test_obsidian_cli_writer_creates_note_by_relative_name(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault_root = root / "vault"
            vault_root.mkdir()
            calls_path = root / "calls.log"
            cli_path = root / "fake-obsidian-cli"
            cli_path.write_text(
                "#!/bin/sh\n"
                f"for arg in \"$@\"; do printf '<%s>\\n' \"$arg\" >> {calls_path}; done\n",
                encoding="utf-8",
            )
            os.chmod(cli_path, 0o755)
            writer = ObsidianCliNoteWriter(
                config=ObsidianConfig(
                    cli_bin=str(cli_path),
                    vault_repo_url="https://github.com/bprager/obs-vault.git",
                    vault_local_path=vault_root,
                    vault_name="scratch-vault",
                ),
                vault_root=vault_root,
            )

            writer.write_note(vault_root / "10 - Logs" / "note.md", "hello")

            self.assertEqual(
                calls_path.read_text(encoding="utf-8").splitlines(),
                [
                    "<create>",
                    "<10 - Logs/note>",
                    "<--vault>",
                    "<scratch-vault>",
                    "<--content>",
                    "<hello>",
                    "<--overwrite>",
                ],
            )

    def test_obsidian_cli_writer_falls_back_to_filesystem_when_cli_is_async(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault_root = root / "vault"
            vault_root.mkdir()
            cli_path = root / "fake-obsidian-cli"
            cli_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            os.chmod(cli_path, 0o755)
            writer = ObsidianCliNoteWriter(
                config=ObsidianConfig(
                    cli_bin=str(cli_path),
                    vault_repo_url="https://github.com/bprager/obs-vault.git",
                    vault_local_path=vault_root,
                    vault_name="scratch-vault",
                ),
                vault_root=vault_root,
            )
            note_path = vault_root / "10 - Logs" / "note.md"

            writer.write_note(note_path, "hello")

            self.assertEqual(note_path.read_text(encoding="utf-8"), "hello")
