"""Refresh and apply one bounded batch inside a caller-protected window.

The caller must hold the cross-host maintenance window for the whole operation.
This helper does not establish that window or authorize live writes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from logbook.bounded_graph_repair import apply_one_batch, rollback_one_batch
from logbook.graph_write_lock import GraphWriteLock
from logbook.memory_graph import MemoryGraphPlan, GraphQueryClient, build_memory_graph_repair_plan


def repair_in_window(plan: MemoryGraphPlan, client: GraphQueryClient, session: Any,
                     lock: GraphWriteLock, journal: Path) -> str:
    if not isinstance(lock, GraphWriteLock) or not lock.held:
        raise ValueError("held writer lock required")
    repair = build_memory_graph_repair_plan(plan, client)
    return apply_one_batch(session, lock, repair, journal)


def recover_in_window(session: Any, lock: GraphWriteLock, journal: Path,
                      expected_batch: str) -> str | None:
    """Recover only the independently identified batch in the protected window."""
    return rollback_one_batch(session, lock, journal, expected_batch, allow_absent=True)
