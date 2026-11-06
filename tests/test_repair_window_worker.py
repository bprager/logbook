import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from logbook.graph_write_lock import GraphWriteLock
from logbook.memory_graph import MemoryGraphPlan, MemoryGraphRepairPlan, MemoryNode
from logbook.repair_window_worker import repair_in_window


class RepairWindowWorkerTests(unittest.TestCase):
    def test_refreshes_graph_before_applying_and_leaves_recovery_record(self):
        plan = MemoryGraphPlan((MemoryNode('logbook:job:000123', ('LogbookJob',), {}),), (), 'now')
        refreshed = MemoryGraphRepairPlan(plan, (), (), plan.nodes, (), (), ())
        with TemporaryDirectory() as tmp, GraphWriteLock(Path(tmp)) as lock:
            session, client = Mock(), Mock()
            journal = Path(tmp) / 'journal'
            with patch('logbook.repair_window_worker.build_memory_graph_repair_plan', return_value=refreshed) as refresh, patch('logbook.repair_window_worker.apply_one_batch', return_value='batch') as apply:
                self.assertEqual(repair_in_window(plan, client, session, lock, journal), 'batch')
            refresh.assert_called_once_with(plan, client)
            apply.assert_called_once_with(session, lock, refreshed, journal)

    def test_refuses_unheld_lock_before_querying(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'held writer lock'):
                repair_in_window(Mock(), Mock(), Mock(), GraphWriteLock(Path(tmp)), Path(tmp) / 'journal')
