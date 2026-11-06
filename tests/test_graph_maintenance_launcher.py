import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from logbook.graph_maintenance_launcher import DockerLaunch, launch
from logbook.graph_maintenance_recovery import DockerRecovery


class GraphMaintenanceLauncherTests(unittest.TestCase):
    def test_recovery_is_scheduled_before_fence_or_worker(self):
        operations = Mock()
        launch(operations)
        self.assertEqual(operations.mock_calls, [
            call.verify_target(), call.prepare_worker(), call.schedule_recovery(),
            call.install_fence(), call.verify_fence(), call.drain_existing(), call.verify_fence(),
            call.start_worker(),
        ])

    def test_no_worker_if_fence_install_fails(self):
        operations = Mock()
        operations.install_fence.side_effect = RuntimeError("failed")
        with self.assertRaises(RuntimeError):
            launch(operations)
        operations.schedule_recovery.assert_called_once()
        operations.start_worker.assert_not_called()

    def test_no_fence_if_recovery_not_scheduled(self):
        operations = Mock()
        operations.schedule_recovery.side_effect = RuntimeError("failed")
        with self.assertRaises(RuntimeError):
            launch(operations)
        operations.install_fence.assert_not_called()
        operations.start_worker.assert_not_called()

    def test_no_worker_if_existing_transactions_do_not_drain(self):
        operations = Mock()
        operations.drain_existing.side_effect = RuntimeError("not drained")
        with self.assertRaises(RuntimeError):
            launch(operations)
        operations.start_worker.assert_not_called()

    def test_real_adapter_builds_worker_timer_and_fence_commands(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            recovery = DockerRecovery("a" * 64, 123, 7687,
                                      "logbook-graph-maintenance-test.service")
            recovery.run = Mock(side_effect=[f'{"a" * 64} 123 true', f'{"a" * 64} 123 true', "", "",
                                             "active", "active", "active", "active"])
            recovery.verify_target = Mock()
            recovery.transactions = Mock(return_value=[])
            recovery.install_fence = Mock()
            launcher = DockerLaunch(recovery, root, root / "worker", root / "recovery.py", 10)
            with patch.object(Path, "stat", return_value=SimpleNamespace(st_uid=0, st_mode=0o700)):
                with patch.object(Path, "exists", return_value=False):
                    launcher.run_window()
            self.assertTrue((root / "worker-start").exists())
            self.assertTrue((root / "setup.lock").exists())
            calls = recovery.run.call_args_list
            self.assertTrue(any("--timer-property=AccuracySec=1ms" in c.args[0] for c in calls))
            self.assertTrue(any("--property=TimeoutStartSec=240s" in c.args[0] for c in calls))
            recovery.install_fence.assert_called_once()
            self.assertEqual(recovery.verify_target.call_count, 2)

    def test_rejects_unsafe_setup_and_changed_target(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            recovery = DockerRecovery("a" * 64, 123, 7687,
                                      "logbook-graph-maintenance-test.service")
            with self.assertRaises(ValueError):
                DockerLaunch(recovery, root, root / "worker", root / "recovery", 9)
            launcher = DockerLaunch(recovery, root, root / "worker", root / "recovery", 10)
            with patch.object(Path, "stat", return_value=SimpleNamespace(st_uid=1, st_mode=0o700)):
                with self.assertRaisesRegex(ValueError, "root-private"):
                    launcher.run_window()
            with patch.object(Path, "stat", side_effect=[SimpleNamespace(st_uid=0, st_mode=0o700),
                                                        SimpleNamespace(st_uid=1, st_mode=0o600)]):
                with self.assertRaisesRegex(ValueError, "root-owned"):
                    launcher.run_window()
            with patch.object(Path, "stat", return_value=SimpleNamespace(st_uid=0, st_mode=0o700)):
                with patch.object(Path, "exists", return_value=True):
                    with self.assertRaisesRegex(ValueError, "directory is not fresh"):
                        launcher.preflight()
            recovery.run = Mock(side_effect=["wrong target"])
            with self.assertRaisesRegex(RuntimeError, "target container changed"):
                launcher.verify_target()
            recovery.verify_target = Mock()
            recovery.run = Mock(return_value="inactive")
            with self.assertRaisesRegex(RuntimeError, "unit is not active"):
                launcher.verify_fence()
            with self.assertRaisesRegex(RuntimeError, "deadline elapsed"):
                launcher.start_worker()
            launcher.gate.touch()
            with self.assertRaisesRegex(RuntimeError, "gate already exists"):
                launcher.prepare_worker()

    def test_operator_retry_requires_stop_proof_and_installed_fence(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            recovery = Mock()
            recovery.unit = 'logbook-graph-maintenance-test.service'
            recovery._proof_valid.return_value = False
            launcher = DockerLaunch(recovery, root, root/'worker', root/'recovery', 10)
            (root/'setup.lock').touch()
            with self.assertRaisesRegex(RuntimeError, 'stop proof'):
                launcher.retry_recovery()
            recovery.verify_target.assert_not_called()

    def test_operator_retry_rejects_active_and_restarts_failed_service(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            recovery = Mock(container_id='a'*64, pid=123, port=7687,
                            unit='logbook-graph-maintenance-test.service')
            recovery._proof_valid.return_value = True
            launcher = DockerLaunch(recovery, root, root/'worker', root/'recovery', 10)
            (root/'setup.lock').touch()
            info = SimpleNamespace(st_uid=0, st_mode=0o700)
            with patch.object(Path, 'stat', return_value=info), patch.object(Path, 'is_symlink', return_value=False):
                recovery.run.return_value = 'active'
                with self.assertRaisesRegex(RuntimeError, 'still active'):
                    launcher.retry_recovery()
                recovery.run.side_effect = ['failed', '', '']
                launcher.retry_recovery()
            self.assertTrue(any('reset-failed' in c.args[0] for c in recovery.run.call_args_list))
            self.assertTrue(any('systemd-run' in c.args[0] for c in recovery.run.call_args_list))

    def test_operator_retry_rejects_untrusted_recovery_file(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            recovery = Mock(unit='logbook-graph-maintenance-test.service')
            recovery._proof_valid.return_value = True
            launcher = DockerLaunch(recovery, root, root/'worker', root/'recovery', 10)
            with patch.object(Path, 'stat', return_value=SimpleNamespace(st_uid=1000, st_mode=0o700)), \
                 patch.object(Path, 'is_symlink', return_value=False):
                with self.assertRaisesRegex(ValueError, 'root-owned'):
                    launcher.retry_recovery()
