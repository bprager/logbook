import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from logbook.graph_maintenance_launcher import DockerLaunch, launch
from logbook.graph_maintenance_recovery import DockerRecovery


class GraphMaintenanceLauncherTests(unittest.TestCase):
    def test_cancel_unstarted_window_prevents_a_late_launch(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            recovery = Mock(unit='logbook-graph-maintenance-test.service')
            launcher = DockerLaunch(recovery, root, root/'worker', root/'recovery', 10)
            self.assertFalse(launcher.cancelled())
            self.assertTrue(launcher.cancel_unstarted())
            self.assertTrue(launcher.cancelled())
            self.assertTrue(launcher.cancel_unstarted())
            with patch.object(Path, 'stat', return_value=SimpleNamespace(st_uid=0, st_mode=0o700)):
                with self.assertRaisesRegex(ValueError, 'directory is not fresh'):
                    launcher.preflight()
            self.assertFalse(launcher.gate.exists())
            recovery.run.assert_not_called()

    def test_cancel_cannot_override_an_already_started_window(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            recovery = Mock(unit='logbook-graph-maintenance-test.service')
            launcher = DockerLaunch(recovery, root, root/'worker', root/'recovery', 10)
            (root/'setup.lock').touch()
            self.assertFalse(launcher.cancel_unstarted())
            self.assertFalse(launcher.cancelled())
            self.assertFalse(launcher.gate.exists())
            recovery.run.assert_not_called()

    def test_launch_and_cancel_cannot_both_claim_the_window(self):
        import os
        import threading
        from concurrent.futures import ThreadPoolExecutor
        for _ in range(30):
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                launcher = DockerLaunch(Mock(unit='logbook-graph-maintenance-test.service'),
                                        root, root/'worker', root/'recovery', 10)
                barrier = threading.Barrier(2)
                def launch_claim():
                    barrier.wait()
                    try:
                        fd = os.open(launcher.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    except FileExistsError:
                        return False
                    os.close(fd)
                    return True
                def cancel_claim():
                    barrier.wait()
                    return launcher.cancel_unstarted()
                with ThreadPoolExecutor(max_workers=2) as pool:
                    launch = pool.submit(launch_claim)
                    cancel = pool.submit(cancel_claim)
                    self.assertNotEqual(launch.result(), cancel.result())
                    self.assertEqual(launcher.cancelled(), cancel.result())

    def test_interrupted_launcher_keeps_setup_lock_until_its_command_finishes(self):
        import fcntl
        import subprocess
        import sys
        import time
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = r"""
import sys
from pathlib import Path
from unittest.mock import patch
from logbook.graph_maintenance_launcher import DockerLaunch
from logbook.graph_maintenance_recovery import DockerRecovery
root = Path(sys.argv[1])
recovery = DockerRecovery('a'*64, 1, 7687, 'logbook-graph-maintenance-test.service')
launcher = DockerLaunch(recovery, root, root/'worker', root/'recovery', 10)
code = 'import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); (p/"child-ready").touch();\nwhile not (p/"child-stop").exists(): time.sleep(0.02)'
with patch.object(launcher, 'preflight'), patch('logbook.graph_maintenance_launcher.launch',
        side_effect=lambda _: recovery.run([sys.executable, '-c', code, str(root)], timeout=30)):
    launcher.run_window()
"""
            parent = subprocess.Popen([sys.executable,'-c',script,str(root)],
                                      stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic()+10
                while not (root/'child-ready').exists():
                    if parent.poll() is not None:
                        self.fail(f'launcher exited early: {parent.stderr.read().decode()}')
                    if time.monotonic()>deadline:
                        self.fail('fixture child did not start')
                    time.sleep(0.02)
                parent.kill()
                parent.wait(timeout=5)
                with (root/'setup.lock').open('r') as lock:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    (root/'child-stop').touch()
                    deadline = time.monotonic()+5
                    while True:
                        try:
                            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            if time.monotonic()>deadline:
                                self.fail('setup lock did not release')
                            time.sleep(0.02)
            finally:
                (root/'child-stop').touch()
                if parent.poll() is None:
                    parent.kill()
                    parent.wait()
                parent.stderr.close()

    def test_guard_is_closed_only_after_recovery_timer_is_scheduled(self):
        recovery = Mock()
        recovery.unit = 'logbook-graph-maintenance-test.service'
        events = []
        recovery.run.side_effect = lambda *args, **kwargs: events.append('timer')
        launcher = DockerLaunch(recovery,Path('/tmp/window'),Path('/tmp/worker'),Path('/tmp/recovery'),10)
        launcher.after_schedule = lambda: events.append('guard')
        launcher.schedule_recovery()
        self.assertEqual(events,['timer','guard'])

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
