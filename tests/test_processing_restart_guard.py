from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch
import io
import subprocess
from types import SimpleNamespace

from logbook.cli import _process_mounted_recorder

from logbook.graph_write_lock import GraphWriteLock
from logbook.graph_maintenance_cross_host import SshMaintenance, coordinate_leased_window
from logbook.processing_restart_guard import arm_guard, clear_guard, require_clear_guard
from logbook.operator_entry import release_fixture_guard, release_processing_guard


class ProcessingRestartGuardTests(TestCase):
    def test_guard_persists_and_blocks_graph_writers_until_matching_clear(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            arm_guard(root, 'window-one')
            with self.assertRaisesRegex(RuntimeError, 'unresolved'):
                require_clear_guard(root)
            with self.assertRaisesRegex(RuntimeError, 'unresolved'):
                with GraphWriteLock(root):
                    pass
            with self.assertRaisesRegex(ValueError, 'different'):
                clear_guard(root, 'window-two')
            clear_guard(root, 'window-one')
            require_clear_guard(root)
            with GraphWriteLock(root):
                pass

    def test_existing_guard_cannot_be_replaced(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            arm_guard(root, 'window-one')
            with self.assertRaises(FileExistsError):
                arm_guard(root, 'window-two')
            with self.assertRaisesRegex(ValueError, 'different'):
                clear_guard(root, 'window-two')

    def test_normal_processing_stops_before_pipeline_when_guard_exists(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            arm_guard(root, 'window-one')
            config = SimpleNamespace(processing_root=root, obsidian=object())
            with patch('logbook.cli.load_app_config', return_value=config), \
                 patch('logbook.cli._process_ingestion_pipeline', side_effect=AssertionError('pipeline ran')) as pipeline:
                self.assertNotEqual(_process_mounted_recorder(root / 'env'), 0)
                pipeline.assert_not_called()

    def test_guard_exists_before_remote_launch_and_remains_if_recovery_unconfirmed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            unit = 'logbook-graph-maintenance-' + 'a' * 32 + '.service'
            remote = SshMaintenance('odin', Path('/key'), Path('/entry'), {'unit': unit})
            remote.preflight = Mock()
            remote.recovered = Mock(return_value=False)
            def launch():
                with self.assertRaisesRegex(RuntimeError, 'unresolved'):
                    require_clear_guard(root)
            remote.launch = Mock(side_effect=launch)
            child = Mock(stdin=io.StringIO(), stdout=io.StringIO('READY\n'))
            child.wait.side_effect = subprocess.TimeoutExpired([], 1)
            with patch('logbook.graph_maintenance_cross_host.subprocess.Popen', return_value=child), \
                 patch('logbook.graph_maintenance_cross_host.select.select', return_value=([child.stdout], [], [])):
                with self.assertRaises(TimeoutError):
                    coordinate_leased_window(SimpleNamespace(processing_root=root), remote, timeout_seconds=1)
            with self.assertRaisesRegex(RuntimeError, 'unresolved'):
                require_clear_guard(root)

    def test_operator_release_requires_matching_confirmed_recovery(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            unit = 'logbook-graph-maintenance-' + 'a' * 32 + '.service'
            config = SimpleNamespace(processing_root=root)
            remote = Mock(request={'unit': unit, 'sandbox_only': True})
            arm_guard(root, unit)
            remote.recovered.return_value = False
            with self.assertRaisesRegex(RuntimeError, 'unconfirmed'):
                release_processing_guard(config, remote)
            with self.assertRaisesRegex(RuntimeError, 'unresolved'):
                require_clear_guard(root)
            remote.recovered.return_value = True
            remote.outcome.side_effect = RuntimeError('invalid outcome')
            with self.assertRaisesRegex(RuntimeError, 'invalid outcome'):
                release_processing_guard(config, remote)
            with self.assertRaisesRegex(RuntimeError, 'unresolved'):
                require_clear_guard(root)
            remote.outcome.side_effect = None
            remote.outcome.return_value = 'retained'
            release_processing_guard(config, remote)
            require_clear_guard(root)

    def test_invalid_window_is_rejected(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'invalid'):
                arm_guard(Path(tmp), '')

    def test_release_refuses_non_fixture_and_missing_unit(self):
        with TemporaryDirectory() as tmp:
            config = SimpleNamespace(processing_root=Path(tmp))
            for request in ({'sandbox_only': False}, {'sandbox_only': True}):
                with self.assertRaises(ValueError):
                    release_processing_guard(config, Mock(request=request))

    def test_release_reads_saved_fixture_state(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            unit = 'fixture-unit'
            arm_guard(root, unit)
            state = {'host':'odin', 'identity':'/key', 'entrypoint':'/entry',
                     'request':{'unit':unit, 'sandbox_only':True}}
            with patch('logbook.operator_entry.read_private_json', return_value=state), \
                 patch('logbook.operator_entry.SshMaintenance') as cls:
                cls.return_value.request = state['request']
                cls.return_value.recovered.return_value = True
                self.assertEqual(release_fixture_guard(SimpleNamespace(processing_root=root), root/'state'), 'released')
            require_clear_guard(root)
