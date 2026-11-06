from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock
from pathlib import Path
from types import SimpleNamespace
import fcntl
import subprocess
import sys
from uuid import uuid4

from logbook.graph_write_lock import GraphWriteLock
from logbook.guarded_graph_repair import apply_batch, rollback_batch
from logbook.memory_graph import MemoryNode
from logbook.graph_maintenance_launcher import with_logbook_lock_descriptors, with_logbook_locks


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
            with self.assertRaisesRegex(ValueError, 'not held'):
                lock.fileno()

    def test_both_live_processing_and_graph_locks_are_held(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = SimpleNamespace(processing_root=root)

            def operation():
                with (root / "pipeline.lock").open("a") as contender:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(BlockingIOError):
                    with GraphWriteLock(root):
                        pass
                return "recovered"

            self.assertEqual(with_logbook_locks(config, operation), "recovered")
            with GraphWriteLock(root):
                pass

    def test_busy_pipeline_never_starts_maintenance(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            operation = Mock()
            with (root / "pipeline.lock").open("a") as held:
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(BlockingIOError):
                    with_logbook_locks(SimpleNamespace(processing_root=root), operation)
            operation.assert_not_called()

    def test_inherited_descriptors_keep_locks_after_parent_closes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            def start_child(pipeline_fd, graph_fd):
                return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                        pass_fds=(pipeline_fd, graph_fd))

            child = with_logbook_lock_descriptors(SimpleNamespace(processing_root=root), start_child)
            try:
                with self.assertRaises(BlockingIOError):
                    with_logbook_locks(SimpleNamespace(processing_root=root), lambda: None)
            finally:
                child.terminate()
                child.wait(timeout=5)
            self.assertIsNone(with_logbook_locks(SimpleNamespace(processing_root=root), lambda: None))
