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
            call.install_fence(), call.verify_fence(), call.start_worker(),
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

    def test_real_adapter_builds_worker_timer_and_fence_commands(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            recovery = DockerRecovery("a" * 64, 123, 7687,
                                      "logbook-graph-maintenance-test.service")
            recovery.run = Mock(side_effect=[f'{"a" * 64} 123 true', "", "", "", "",
                                             "active", "active"])
            recovery.verify_target = Mock()
            launcher = DockerLaunch(recovery, root, root / "worker", root / "recovery.py", 10)
            with patch.object(Path, "stat", return_value=SimpleNamespace(st_uid=0, st_mode=0o700)):
                with patch.object(Path, "exists", return_value=False):
                    launcher.run_window()
            self.assertTrue((root / "worker-start").exists())
            self.assertTrue((root / "setup.lock").exists())
            calls = recovery.run.call_args_list
            self.assertTrue(any("--timer-property=AccuracySec=1ms" in c.args[0] for c in calls))
            self.assertTrue(any("iptables" in c.args[0] for c in calls))
            recovery.verify_target.assert_called_once()

    def test_rejects_unsafe_setup_and_uncovered_ipv6(self):
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
            recovery.run = Mock(side_effect=["wrong target"])
            with self.assertRaisesRegex(RuntimeError, "target container changed"):
                launcher.verify_target()
            recovery.run = Mock(side_effect=[f'{"a" * 64} 123 true', "LISTEN IPv6"])
            with self.assertRaisesRegex(RuntimeError, "IPv6 listener"):
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
