import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from logbook.graph_write_lock import GraphWriteLock
from logbook.repair_window_worker import recover_in_window

BATCH = '00000000-0000-0000-0000-000000000001'


class FakeResult:
    def __init__(self, rows):
        self.rows = rows
    def __iter__(self):
        return iter(self.rows)
    def single(self):
        return self.rows[0]


class EmptyBatchTransaction:
    def __init__(self, nodes=0, relationships=0):
        self.nodes, self.relationships = nodes, relationships
    def run(self, query, **kwargs):
        if 'RETURN properties(m)' in query:
            return FakeResult([])
        if 'MATCH (n) WHERE n._logbook_repair_batch' in query:
            return FakeResult([{'n': self.nodes}])
        if 'MATCH ()-[r]->()' in query:
            return FakeResult([{'n': self.relationships}])
        raise AssertionError(query)


class RecoveryTests(unittest.TestCase):
    def test_absent_batch_is_safe_to_retry(self):
        with TemporaryDirectory() as tmp, GraphWriteLock(Path(tmp)) as lock:
            journal = Path(tmp) / 'journal'
            journal.write_text(json.dumps({'schema': 1, 'batch': BATCH}))
            session = Mock()
            session.execute_write.side_effect = lambda fn: fn(EmptyBatchTransaction())
            self.assertEqual(recover_in_window(session, lock, journal, BATCH), 'absent')

    def test_orphaned_batch_records_refuse_recovery(self):
        with TemporaryDirectory() as tmp, GraphWriteLock(Path(tmp)) as lock:
            journal = Path(tmp) / 'journal'
            journal.write_text(json.dumps({'schema': 1, 'batch': BATCH}))
            for nodes, relationships in ((1, 0), (0, 1)):
                session = Mock()
                session.execute_write.side_effect = lambda fn: fn(EmptyBatchTransaction(nodes, relationships))
                with self.assertRaisesRegex(ValueError, 'orphaned'):
                    recover_in_window(session, lock, journal, BATCH)
