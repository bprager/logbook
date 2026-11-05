from tempfile import TemporaryDirectory
from unittest import TestCase
from pathlib import Path
from uuid import uuid4

from logbook.graph_write_lock import GraphWriteLock
from logbook.guarded_graph_repair import apply_batch, rollback_batch
from logbook.memory_graph import MemoryNode


class GraphWriterGuardTests(TestCase):
    def test_repair_and_rollback_refuse_without_a_held_writer_lock(self):
        node = MemoryNode('logbook:job:000123', ('LogbookJob',), {})
        with self.assertRaisesRegex(ValueError, 'writer lock'):
            apply_batch(None, str(uuid4()), [node], [])
        with self.assertRaisesRegex(ValueError, 'writer lock'):
            rollback_batch(None, str(uuid4()))

    def test_released_lock_no_longer_authorizes_repair(self):
        with TemporaryDirectory() as tmp:
            lock = GraphWriteLock(Path(tmp))
            with lock:
                self.assertTrue(lock.held)
            self.assertFalse(lock.held)
            with self.assertRaisesRegex(ValueError, 'writer lock'):
                rollback_batch(None, str(uuid4()), lock=lock)
