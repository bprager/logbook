"""Coordinate cooperating local graph writers for a complete operation."""

import fcntl
from pathlib import Path


class GraphWriteLock:
    def __init__(self, processing_root: Path):
        self.processing_root = processing_root
        self._file = None

    @property
    def held(self) -> bool:
        return self._file is not None and not self._file.closed

    def __enter__(self):
        if self.held:
            raise ValueError("writer lock is already held")
        self.processing_root.mkdir(parents=True, exist_ok=True)
        lock = (self.processing_root / "graph-write.lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            lock.close()
            raise
        self._file = lock
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._file.close()
        self._file = None
