"""One create-only repair batch with a durable, content-free recovery record.

The caller must supply a fresh plan and an independently protected graph window.
This module does not activate a production repair path.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from logbook.graph_write_lock import GraphWriteLock
from logbook.guarded_graph_repair import apply_batch, rollback_batch
from logbook.memory_graph import MemoryGraphRepairPlan


def apply_one_batch(session: Any, lock: GraphWriteLock, plan: MemoryGraphRepairPlan,
                    journal: Path) -> str:
    nodes = list(plan.missing_nodes)
    relationships = list(plan.missing_relationships)
    if not isinstance(lock, GraphWriteLock) or not lock.held:
        raise ValueError("held writer lock required")
    if not 1 <= len(nodes) + len(relationships) <= 500:
        raise ValueError("repair must contain between 1 and 500 records")
    batch = str(uuid.uuid4())
    record = {"schema": 1, "batch": batch, "nodes": len(nodes),
              "relationships": len(relationships)}
    fd = os.open(journal, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        json.dump(record, output, sort_keys=True)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    directory = os.open(journal.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    apply_batch(session, batch, nodes, relationships, lock=lock)
    return batch


def rollback_one_batch(session: Any, lock: GraphWriteLock, journal: Path,
                       expected_batch: str) -> None:
    if not isinstance(lock, GraphWriteLock) or not lock.held:
        raise ValueError("held writer lock required")
    fd = os.open(journal, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "r", encoding="utf-8") as source:
        record = json.load(source)
    if (not isinstance(record, dict) or record.get("schema") != 1
            or record.get("batch") != expected_batch
            or str(uuid.UUID(expected_batch)) != expected_batch):
        raise ValueError("recovery record does not match expected batch")
    rollback_batch(session, expected_batch, lock=lock)
