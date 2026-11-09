"""Prepare one private repair plan from a consistent ledger snapshot."""
from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import stat
import sys
import tempfile
from pathlib import Path

from logbook.config import AppConfig, load_app_config
from logbook.graph_maintenance_worker import private_directory
from logbook.memory_graph import build_memory_graph_plan


def _copy_artifact(root: Path, relative: Path, destination: Path, *, optional: bool = False) -> bool:
    if relative.is_absolute() or not relative.parts or '..' in relative.parts:
        raise ValueError('artifact path outside processing root')
    directories = []
    try:
        directories.append(os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        for component in relative.parts[:-1]:
            directories.append(os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                       dir_fd=directories[-1]))
        fd = os.open(relative.parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directories[-1])
        with os.fdopen(fd, 'rb') as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > 50000000:
                raise ValueError('invalid repair artifact')
            content = source.read(50000001)
            after = os.fstat(source.fileno())
        if (len(content) > 50000000 or
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise ValueError('repair artifact changed while reading')
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        return True
    except FileNotFoundError:
        if optional:
            return False
        raise ValueError('referenced transcript missing from repair inputs') from None
    finally:
        for directory in reversed(directories):
            os.close(directory)


def prepare_job_plan(config: AppConfig, job_id: int, destination: Path) -> tuple[int, int]:
    if isinstance(job_id, bool) or job_id < 1:
        raise ValueError('positive job ID required')
    private_directory(destination.parent)
    with tempfile.TemporaryDirectory() as temporary:
        snapshot = Path(temporary) / 'ledger.sqlite'
        with sqlite3.connect(config.sqlite_path.resolve().as_uri() + '?mode=ro', uri=True) as source:
            with sqlite3.connect(snapshot) as target:
                source.backup(target)
        inputs = Path(temporary) / 'inputs'
        inputs.mkdir()
        with sqlite3.connect(snapshot) as ledger:
            row = ledger.execute('SELECT diarization_path, transcript_path FROM recording_jobs WHERE id = ?',
                                 (job_id,)).fetchone()
            if row and (row[0] or row[1]):
                try:
                    relative = Path(row[0] or row[1]).relative_to(config.processing_root)
                except ValueError:
                    raise ValueError('artifact path outside processing root') from None
                copied = inputs / 'transcript.json'
                _copy_artifact(config.processing_root, relative, copied)
                field = 'diarization_path' if row[0] else 'transcript_path'
                ledger.execute(f'UPDATE recording_jobs SET {field} = ? WHERE id = ?', (str(copied), job_id))
            insight = Path('insights') / f'job-{job_id:06d}.insights.json'
            _copy_artifact(config.processing_root, insight, inputs / insight, optional=True)
        plan = build_memory_graph_plan(dataclasses.replace(config, sqlite_path=snapshot, processing_root=inputs),
                                       job_id=job_id)
        if row and (row[0] or row[1]) and not any(
            node.properties.get('source_kind') == 'transcript_segment'
            and node.properties.get('job_id') == job_id for node in plan.nodes
        ):
            raise ValueError('referenced transcript missing from repair plan')
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
