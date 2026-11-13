"""Bounded private startup evidence, collected before an isolated process dies."""

from __future__ import annotations

from collections import deque
import json
import os
from pathlib import Path
import re
import stat
import sys
import threading
import time
import uuid


def capture(pid: str, proc: Path = Path("/proc")) -> dict:
    if not re.fullmatch(r"[1-9][0-9]*", pid):
        raise ValueError("positive isolated process ID required")
    base = proc / pid

    def read(path: Path, tail: bool = False) -> str:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("regular diagnostic file required")
                if tail:
                    source.seek(max(0, info.st_size - 4096))
                return source.read(4096).decode(errors="replace")
        except (OSError, ValueError) as error:
            return str(error)

    result = {
        "alive": base.exists(),
        "monotonic": time.monotonic(),
        "status": read(base / "status"),
        "listeners": read(base / "net/tcp"),
        "cgroup": {
            name: read(base / "root/sys/fs/cgroup" / name)
            for name in ("memory.current", "memory.max", "memory.events", "cpu.stat")
        },
        "logs": {},
    }
    directory = base / "root/var/log/memgraph"
    try:
        for path in sorted(directory.iterdir())[:4]:
            result["logs"][path.name] = read(path, True)
    except OSError as error:
        result["logs_error"] = str(error)
    return result


class StartupEvidence:
    def __init__(self, pid: str, directory: Path):
        self.pid, self.directory = pid, directory
        self.samples: deque = deque(maxlen=6)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.poll, daemon=True)

    def sample(self) -> None:
        self.samples.append(capture(self.pid))

    def poll(self) -> None:
        while not self.stop.wait(1):
            self.sample()

    def __enter__(self):
        self.sample()
        self.thread.start()
        return self

    def __exit__(self, kind, error, traceback):
        self.stop.set()
        self.thread.join(timeout=5)
        frames = list(self.samples)
        path = self.directory / ("offline-startup-" + uuid.uuid4().hex + ".json")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as output:
                json.dump(frames, output)
                output.flush()
                os.fsync(output.fileno())
        except OSError as failure:
            print(json.dumps({"startup_evidence_write_error": str(failure)}), file=sys.stderr)
        if kind is not None:
            alive = [frame for frame in frames if frame["alive"]]
            print(
                json.dumps(
                    {
                        "offline_startup_evidence": alive[-1] if alive else frames[-1],
                        "private_evidence": str(path),
                    }
                ),
                file=sys.stderr,
                flush=True,
            )
        return False
