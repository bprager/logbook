import json
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from logbook.bounded_graph_repair import apply_one_batch, rollback_one_batch
from logbook.graph_write_lock import GraphWriteLock
from logbook.memory_graph import MemoryGraphPlan, MemoryGraphRepairPlan, MemoryNode


def repair_plan(nodes):
    plan = MemoryGraphPlan(tuple(nodes), (), "fixture")
    return MemoryGraphRepairPlan(plan, (), (), tuple(nodes), (), (), ())


class BoundedGraphRepairTests(unittest.TestCase):
    def test_persists_recovery_id_before_applying_and_rolls_back(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "journal.json"
            node = MemoryNode("logbook:job:000123", ("LogbookJob",), {})
            with GraphWriteLock(root) as lock:
                def apply(session, batch, nodes, relationships, *, lock):
                    self.assertEqual(json.loads(path.read_text())["batch"], batch)
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                with patch("logbook.bounded_graph_repair.apply_batch", side_effect=apply):
                    batch = apply_one_batch(Mock(), lock, repair_plan([node]), path)
                with patch("logbook.bounded_graph_repair.rollback_batch") as rollback:
                    rollback_one_batch(Mock(), lock, path, batch)
                    self.assertEqual(rollback.call_args.args[1], batch)

    def test_rejects_empty_oversize_and_existing_journal(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            node = MemoryNode("logbook:job:000123", ("LogbookJob",), {})
            with GraphWriteLock(root) as lock:
                for nodes in ([], [node] * 501):
                    with self.assertRaises(ValueError):
                        apply_one_batch(Mock(), lock, repair_plan(nodes), root / "journal")
                (root / "journal").write_text("do not overwrite")
                with self.assertRaises(FileExistsError):
                    apply_one_batch(Mock(), lock, repair_plan([node]), root / "journal")
                self.assertEqual((root / "journal").read_text(), "do not overwrite")

    def test_recovery_refuses_wrong_batch(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "journal.json"
            path.write_text('{"batch":"00000000-0000-0000-0000-000000000001"}')
            with GraphWriteLock(root) as lock, self.assertRaises(ValueError):
                rollback_one_batch(Mock(), lock, path, "00000000-0000-0000-0000-000000000002")

    def test_failed_apply_retains_recovery_record(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "journal.json"
            node = MemoryNode("logbook:job:000123", ("LogbookJob",), {})
            with GraphWriteLock(root) as lock:
                with patch("logbook.bounded_graph_repair.apply_batch", side_effect=RuntimeError("offline")):
                    with self.assertRaisesRegex(RuntimeError, "offline"):
                        apply_one_batch(Mock(), lock, repair_plan([node]), path)
                self.assertEqual(json.loads(path.read_text())["schema"], 1)

    def test_unheld_lock_rejects_apply_and_rollback(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = GraphWriteLock(root)
            node = MemoryNode("logbook:job:000123", ("LogbookJob",), {})
            with self.assertRaisesRegex(ValueError, "held writer lock"):
                apply_one_batch(Mock(), lock, repair_plan([node]), root / "journal")
            with self.assertRaisesRegex(ValueError, "held writer lock"):
                rollback_one_batch(Mock(), lock, root / "journal", "not-read")
