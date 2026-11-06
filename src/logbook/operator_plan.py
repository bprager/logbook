"""Prepare one private repair plan from a consistent ledger snapshot."""
from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from logbook.config import AppConfig, load_app_config
from logbook.graph_maintenance_worker import private_directory
from logbook.memory_graph import build_memory_graph_plan


def prepare_job_plan(config: AppConfig, job_id: int, destination: Path) -> tuple[int, int]:
    if isinstance(job_id, bool) or job_id < 1:
        raise ValueError('positive job ID required')
    private_directory(destination.parent)
    with tempfile.TemporaryDirectory() as temporary:
        snapshot = Path(temporary) / 'ledger.sqlite'
        with sqlite3.connect(config.sqlite_path.resolve().as_uri() + '?mode=ro', uri=True) as source:
            with sqlite3.connect(snapshot) as target:
                source.backup(target)
        plan = build_memory_graph_plan(dataclasses.replace(config, sqlite_path=snapshot), job_id=job_id)
    if not plan.nodes:
        raise ValueError('job not found in ledger')
    data = (json.dumps(dataclasses.asdict(plan), sort_keys=True) + '\n').encode()
    if len(data) > 5000000:
        raise ValueError('plan exceeds private worker limit')
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    directory = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return plan.node_count, plan.relationship_count


if __name__ == '__main__':  # pragma: no cover
    if len(sys.argv) != 4:
        raise SystemExit('usage: python -m logbook.operator_plan ENV JOB_ID PRIVATE_PLAN')
    nodes, relationships = prepare_job_plan(load_app_config(Path(sys.argv[1])), int(sys.argv[2]), Path(sys.argv[3]))
    print(f'plan prepared: {nodes} nodes, {relationships} links')
