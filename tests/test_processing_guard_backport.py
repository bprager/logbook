import fcntl
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from logbook.cli import _process_mounted_recorder, main


class ProcessingGuardBackportTests(TestCase):
    def test_pending_repair_blocks_normal_processing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'graph-maintenance.pending').write_text('fixture-window\n')
            config = SimpleNamespace(processing_root=root, obsidian=object())
            with patch('logbook.cli.load_app_config', return_value=config), \
                 patch('logbook.cli._process_ingestion_pipeline', side_effect=AssertionError('pipeline ran')):
                self.assertNotEqual(_process_mounted_recorder(root / 'env'), 0)

    def test_pending_repair_blocks_manual_graph_sync_and_repair(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'graph-maintenance.pending').write_text('fixture-window\n')
            config = SimpleNamespace(processing_root=root)
            with patch('logbook.cli.load_app_config', return_value=config), \
                 patch('logbook.cli._memory_graph_sync', side_effect=AssertionError('graph sync ran')), \
                 patch('logbook.cli._memory_graph_repair', side_effect=AssertionError('graph repair ran')):
                for command in ('memory-graph-sync', 'memory-graph-repair'):
                    self.assertNotEqual(main([command, '--env', str(root / 'env'), '--execute']), 0)

    def test_graph_operation_holds_the_shared_lock_and_continues_when_clear(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = SimpleNamespace(processing_root=root)
            def sync(*args, **kwargs):
                with (root / 'graph-write.lock').open('a') as contender:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return 0
            with patch('logbook.cli.load_app_config', return_value=config), \
                 patch('logbook.cli._memory_graph_sync', side_effect=sync):
                self.assertEqual(main(['memory-graph-sync', '--env', str(root / 'env'), '--execute']), 0)
                with (root / 'graph-write.lock').open('a') as held:
                    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.assertNotEqual(main(['memory-graph-sync', '--env', str(root / 'env'), '--execute']), 0)

    def test_processing_resumes_when_no_guard_exists(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = SimpleNamespace(processing_root=root, obsidian=object())
            with patch('logbook.cli.load_app_config', return_value=config), \
                 patch('logbook.cli._process_ingestion_pipeline', return_value=0), \
                 patch('logbook.cli.pending_remote_graph_jobs', return_value=()):
                self.assertEqual(_process_mounted_recorder(root / 'env'), 0)
