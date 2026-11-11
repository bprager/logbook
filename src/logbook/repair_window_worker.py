"""Refresh and apply one bounded batch inside a caller-protected window.

The caller must hold the cross-host maintenance window for the whole operation.
This helper does not establish that window or authorize live writes.
"""
from __future__ import annotations

from pathlib import Path
from dataclasses import replace
from typing import Any

from logbook.bounded_graph_repair import apply_one_batch, rollback_one_batch
from logbook.graph_write_lock import GraphWriteLock
from logbook.memory_graph import MemoryGraphPlan, GraphQueryClient, build_memory_graph_repair_plan
from logbook.graph_repair_policy import MAX_BATCH_RECORDS


def repair_in_window(plan: MemoryGraphPlan, client: GraphQueryClient, session: Any,
                     lock: GraphWriteLock, journal: Path) -> str:
    if not isinstance(lock, GraphWriteLock) or not lock.held:
        raise ValueError("held writer lock required")
    repair = build_memory_graph_repair_plan(plan, client)
    if len(repair.missing_nodes) + len(repair.missing_relationships) > MAX_BATCH_RECORDS:
        # New nodes precede links so every selected link's source-plan endpoints
        # are already present or are created in this same transaction.
        nodes = repair.missing_nodes[:MAX_BATCH_RECORDS]
        links = repair.missing_relationships[:MAX_BATCH_RECORDS - len(nodes)]
        repair = replace(repair, missing_nodes=nodes, missing_relationships=links)
    return apply_one_batch(session, lock, repair, journal)


def recover_in_window(session: Any, lock: GraphWriteLock, journal: Path,
                      expected_batch: str) -> str | None:
    """Recover only the independently identified batch in the protected window."""
    return rollback_one_batch(session, lock, journal, expected_batch, allow_absent=True)
