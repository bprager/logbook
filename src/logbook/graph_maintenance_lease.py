"""Independent inherited-lock holder for an experimental maintenance window."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from logbook.graph_maintenance_cross_host import SshMaintenance


def main() -> None:
    if len(sys.argv) != 3:
        raise ValueError("two inherited lock descriptors required")
    for value in sys.argv[1:]:
        os.fstat(int(value))
    payload = json.loads(sys.stdin.readline())
    remote = SshMaintenance(payload["host"], Path(payload["identity"]),
                            Path(payload["entrypoint"]), payload["request"])
    print("READY", flush=True)
    while True:
        try:
            if remote.recovered():
                return
        except Exception:
            pass
        time.sleep(0.5)


if __name__ == "__main__":  # pragma: no cover
    main()
