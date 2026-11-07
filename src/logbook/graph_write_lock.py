"""Coordinate cooperating graph writers with graph maintenance."""
import fcntl
from contextlib import contextmanager
from pathlib import Path

from logbook.processing_restart_guard import require_clear_guard


@contextmanager
def graph_write_lock(processing_root: Path):
    processing_root.mkdir(parents=True, exist_ok=True)
    with (processing_root / 'graph-write.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require_clear_guard(processing_root)
        yield
