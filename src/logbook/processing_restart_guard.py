"""Durable stop marker for cooperating processing-host graph writers."""
from __future__ import annotations

import os
from pathlib import Path


GUARD = "graph-maintenance.pending"


def _sync_directory(root: Path) -> None:
    fd = os.open(root, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def arm_guard(root: Path, window: str) -> None:
    if not window or '\n' in window:
        raise ValueError('invalid maintenance window')
    fd = os.open(root / GUARD, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as guard:
        guard.write(window + '\n')
        guard.flush()
        os.fsync(guard.fileno())
    _sync_directory(root)


def require_clear_guard(root: Path) -> None:
    if os.path.lexists(root / GUARD):
        raise RuntimeError('graph maintenance unresolved; processing is blocked')


def clear_guard(root: Path, window: str) -> None:
    path = root / GUARD
    if path.is_symlink() or path.read_text(encoding='utf-8') != window + '\n':
        raise ValueError('different maintenance window')
    path.unlink()
    _sync_directory(root)
