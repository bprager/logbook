from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from logbook.config import ObsidianConfig
from logbook.markdown import atomic_write_text


class NoteWriteError(RuntimeError):
    """Raised when a routed note cannot be written."""


class NoteWriter(Protocol):
    def write_note(self, path: Path, content: str) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class FilesystemNoteWriter:
    def write_note(self, path: Path, content: str) -> None:
        atomic_write_text(path, content)


@dataclass(frozen=True)
class ObsidianCliNoteWriter:
    config: ObsidianConfig
    vault_root: Path

    def write_note(self, path: Path, content: str) -> None:
        note_name = _note_name(path=path, vault_root=self.vault_root)
        vault_name = self.config.vault_name or self.config.vault_local_path.name
        command = [
            self.config.cli_bin,
            "create",
            note_name,
            "--vault",
            vault_name,
            "--content",
            content,
            "--overwrite",
        ]
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise NoteWriteError(
                f"obsidian-cli create failed for {note_name}: {detail or completed.returncode}"
            )
        if not path.exists():
            atomic_write_text(path, content)


def _note_name(path: Path, vault_root: Path) -> str:
    try:
        relative = path.relative_to(vault_root)
    except ValueError as error:
        raise NoteWriteError(f"note path is outside vault root: {path}") from error
    if relative.suffix == ".md":
        relative = relative.with_suffix("")
    return relative.as_posix()
