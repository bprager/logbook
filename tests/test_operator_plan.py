import sqlite3
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from logbook.graph_maintenance_worker import load_plan
from logbook.memory_graph import MemoryGraphPlan, MemoryNode
from logbook.operator_plan import prepare_job_plan


class OperatorPlanTests(unittest.TestCase):
    def test_copies_ledger_and_writes_private_non_overwriting_plan(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root/'ledger.db'
            with sqlite3.connect(ledger) as db:
                db.execute('CREATE TABLE sample(value TEXT)')
                db.execute("INSERT INTO sample VALUES ('snapshot')")
            node = MemoryNode('logbook:job:000123', ('LogbookJob',), {})
            plan = MemoryGraphPlan((node,), (), 'now')
            def build(snapshot_config, job_id):
                self.assertNotEqual(snapshot_config.sqlite_path, ledger)
                with sqlite3.connect(snapshot_config.sqlite_path) as db:
                    self.assertEqual(db.execute('SELECT value FROM sample').fetchone()[0], 'snapshot')
                self.assertEqual(job_id, 123)
                return plan
            from dataclasses import dataclass
            @dataclass(frozen=True)
            class Config:
                sqlite_path: Path
            with patch('logbook.operator_plan.build_memory_graph_plan', side_effect=build):
                result = prepare_job_plan(Config(ledger), 123, root/'plan.json')
                self.assertEqual(result, (1, 0))
                self.assertEqual(load_plan(root/'plan.json'), plan)
                self.assertEqual(stat.S_IMODE((root/'plan.json').stat().st_mode), 0o600)
                with self.assertRaises(FileExistsError):
                    prepare_job_plan(Config(ledger), 123, root/'plan.json')

    def test_invalid_job_rejected(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'positive'):
                prepare_job_plan(SimpleNamespace(), 0, Path(tmp)/'plan')

    def test_missing_job_and_oversized_plan_are_not_written(self):
        from dataclasses import dataclass
        @dataclass(frozen=True)
        class Config:
            sqlite_path: Path
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root/'ledger.db'
            with sqlite3.connect(ledger) as db:
                db.execute('CREATE TABLE sample(value TEXT)')
            empty = MemoryGraphPlan((), (), 'now')
            large = MemoryGraphPlan((MemoryNode('logbook:job:000123', ('LogbookJob',),
                                                {'payload': 'x'*5000001}),), (), 'now')
            with patch('logbook.operator_plan.build_memory_graph_plan', side_effect=[empty, large]):
                with self.assertRaisesRegex(ValueError, 'not found'):
                    prepare_job_plan(Config(ledger), 123, root/'plan')
                with self.assertRaisesRegex(ValueError, 'limit'):
                    prepare_job_plan(Config(ledger), 123, root/'plan')
            self.assertFalse((root/'plan').exists())
