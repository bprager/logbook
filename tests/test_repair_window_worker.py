import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from logbook.graph_write_lock import GraphWriteLock
from logbook.memory_graph import MemoryGraphPlan, MemoryGraphRepairPlan, MemoryNode, MemoryRelationship
from logbook.repair_window_worker import repair_in_window


class RepairWindowWorkerTests(unittest.TestCase):
    def test_large_plan_advances_in_bounded_windows_with_nodes_before_links(self):
        job = 'logbook:job:000123'
        nodes = (MemoryNode(job, ('LogbookJob',), {}),) + tuple(
            MemoryNode(job + f':segment:{i:04d}', ('TranscriptSegment',), {})
            for i in range(600))
        links = tuple(MemoryRelationship(id=job + f':has-segment:{i:04d}', type='HAS_SEGMENT',
                                        start_id=job, end_id=node.id, properties={})
                      for i, node in enumerate(nodes[1:]))
        plan = MemoryGraphPlan(nodes, links, 'fixture')
        present_nodes, present_links = set(), set()
        sizes = []
        def fresh(_plan, _client):
            return MemoryGraphRepairPlan(plan, (), (),
                tuple(n for n in nodes if n.id not in present_nodes),
                tuple(r for r in links if r.id not in present_links), (), ())
        def apply(_session, _lock, repair, _journal):
            size = len(repair.missing_nodes) + len(repair.missing_relationships)
            self.assertLessEqual(size, 500)
            self.assertGreater(size, 0)
            present_nodes.update(n.id for n in repair.missing_nodes)
            for rel in repair.missing_relationships:
                self.assertIn(rel.start_id, present_nodes)
                self.assertIn(rel.end_id, present_nodes)
            present_links.update(r.id for r in repair.missing_relationships)
            sizes.append(size)
            return 'batch'
        with TemporaryDirectory() as tmp, GraphWriteLock(Path(tmp)) as lock, \
             patch('logbook.repair_window_worker.build_memory_graph_repair_plan', side_effect=fresh), \
             patch('logbook.repair_window_worker.apply_one_batch', side_effect=apply):
            for i in range(3):
                repair_in_window(plan, Mock(), Mock(), lock, Path(tmp) / str(i))
        self.assertEqual(sizes, [500, 500, 201])
        self.assertEqual(present_nodes, {n.id for n in nodes})
        self.assertEqual(present_links, {r.id for r in links})

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

    def test_recovery_requires_matching_durable_record(self):
        import json
        from logbook.repair_window_worker import recover_in_window
        batch = '00000000-0000-0000-0000-000000000001'
        with TemporaryDirectory() as tmp, GraphWriteLock(Path(tmp)) as lock:
            journal = Path(tmp) / 'journal'
            journal.write_text(json.dumps({'schema': 1, 'batch': batch}))
            with patch('logbook.bounded_graph_repair.rollback_batch') as rollback:
                with self.assertRaisesRegex(ValueError, 'does not match'):
                    recover_in_window(Mock(), lock, journal, '00000000-0000-0000-0000-000000000002')
                rollback.assert_not_called()
                recover_in_window(Mock(), lock, journal, batch)
                self.assertEqual(rollback.call_count, 1)
