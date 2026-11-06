import fcntl
import io
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from logbook.graph_maintenance_cross_host import (
    SshMaintenance, coordinate_leased_window, coordinate_window, recovery_confirmed,
)
from logbook.graph_maintenance_remote import main
from logbook.graph_maintenance_lease import main as lease_main


class CrossHostMaintenanceTests(unittest.TestCase):
    def test_disconnect_keeps_locks_until_recovery_is_confirmed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = SimpleNamespace(processing_root=root)
            launch = Mock(side_effect=ConnectionError("disconnected"))
            calls = []

            def recovered():
                with (root / "pipeline.lock").open("a") as contender:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                calls.append(1)
                return len(calls) == 2

            with patch("logbook.graph_maintenance_cross_host.time.sleep"):
                with self.assertRaisesRegex(ConnectionError, "disconnected"):
                    coordinate_window(config, launch, recovered, timeout_seconds=2)
            self.assertEqual(len(calls), 2)

    def test_unconfirmed_recovery_fails(self):
        with TemporaryDirectory() as tmp:
            config = SimpleNamespace(processing_root=Path(tmp))
            with self.assertRaisesRegex(RuntimeError, "recovery was not confirmed"):
                coordinate_window(config, lambda: None, lambda: False, timeout_seconds=0)

    def test_confirmation_requires_completed_recovery_and_no_fence(self):
        ops = Mock()
        ops.container_id = "a" * 64
        ops.pid = 123
        ops.unit = "logbook-graph-maintenance-test.service"
        ops.table = "logbook_fixture"
        ops.run.side_effect = [f'{"a" * 64} 123 true', "12345", "success", "active",
                               "exited", "inactive", '{"nftables":[{"metainfo":{}}]}']
        self.assertTrue(recovery_confirmed(ops, "logbook-graph-maintenance-test-recovery"))

    def test_unstarted_recovery_is_never_confirmed(self):
        ops = Mock()
        ops.container_id, ops.pid = "a" * 64, 123
        ops.run.side_effect = [f'{"a" * 64} 123 true', "0"]
        self.assertFalse(recovery_confirmed(ops, "test-recovery"))
        ops.run.side_effect = [f'{"a" * 64} 123 true', "123", "failure"]
        self.assertFalse(recovery_confirmed(ops, "test-recovery"))
        ops.run.side_effect = [f'{"a" * 64} 123 true', "123", "success", "active", "exited", "active"]
        self.assertFalse(recovery_confirmed(ops, "test-recovery"))

    def test_success_retries_temporary_confirmation_failure(self):
        with TemporaryDirectory() as tmp:
            config = SimpleNamespace(processing_root=Path(tmp))
            recovered = Mock(side_effect=[ConnectionError("offline"), True])
            with patch("logbook.graph_maintenance_cross_host.time.sleep"):
                coordinate_window(config, lambda: None, recovered, timeout_seconds=2)
            self.assertEqual(recovered.call_count, 2)

    def test_confirmation_rejects_changed_target_and_existing_fence(self):
        ops = Mock(container_id="a" * 64, pid=123, unit="test.service", table="fence")
        ops.run.return_value = "changed"
        self.assertFalse(recovery_confirmed(ops, "recovery"))
        identity = f'{"a" * 64} 123 true'
        ops.run.side_effect = [identity, "12", "success", "active", "exited", "inactive",
                               '{"nftables":[{"metainfo":{}},{"table":{"family":"inet","name":"fence"}}]}']
        self.assertFalse(recovery_confirmed(ops, "recovery"))
        ops.run.side_effect = [identity, "12", "success", "active", "exited", "inactive",
                               '{"nftables":[]}']
        with self.assertRaisesRegex(RuntimeError, "incomplete firewall"):
            recovery_confirmed(ops, "recovery")

    def test_ssh_response_and_failures(self):
        request = {"fixture": True}
        remote = SshMaintenance("odin", Path("/key"), Path("/entry"), request)
        with patch("logbook.graph_maintenance_cross_host.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "LAUNCHED\n", "")
            remote.launch()
            run.return_value = subprocess.CompletedProcess([], 0, "RECOVERED\n", "")
            self.assertTrue(remote.recovered())
            run.return_value = subprocess.CompletedProcess([], 0, "PENDING\n", "")
            self.assertFalse(remote.recovered())
            with self.assertRaises(ConnectionError):
                remote.launch()
            run.return_value = subprocess.CompletedProcess([], 1, "", "error")
            with self.assertRaises(ConnectionError):
                remote.recovered()
            run.side_effect = subprocess.TimeoutExpired([], 1)
            with self.assertRaises(TimeoutError):
                remote.recovered()
        with self.assertRaises(ValueError):
            remote.invoke("invalid")
        with self.assertRaises(ValueError):
            SshMaintenance("-bad", Path("/key"), Path("/entry"), request)
        with self.assertRaises(ValueError):
            SshMaintenance("odin", Path("key"), Path("/entry"), request)

    def test_remote_entrypoint_launch_and_status(self):
        request = '{"container_id":"id","pid":1,"port":2,"unit":"unit",' \
                  '"private_dir":"/private","worker":"/worker",' \
                  '"recovery_file":"/recovery","seconds":10}'
        with patch("logbook.graph_maintenance_remote.DockerRecovery"), \
             patch("logbook.graph_maintenance_remote.DockerLaunch") as launcher, \
             patch("logbook.graph_maintenance_remote.recovery_confirmed", return_value=True), \
             patch("sys.stdin", io.StringIO(request)), patch("sys.stdout", new_callable=io.StringIO) as out, \
             patch("sys.argv", ["remote", "launch"]):
            main()
            launcher.return_value.run_window.assert_called_once()
            self.assertEqual(out.getvalue().strip(), "LAUNCHED")
        with patch("logbook.graph_maintenance_remote.DockerRecovery"), \
             patch("logbook.graph_maintenance_remote.DockerLaunch"), \
             patch("logbook.graph_maintenance_remote.recovery_confirmed", return_value=False), \
             patch("sys.stdin", io.StringIO(request)), patch("sys.stdout", new_callable=io.StringIO) as out, \
             patch("sys.argv", ["remote", "status"]):
            main()
            self.assertEqual(out.getvalue().strip(), "PENDING")
        with patch("sys.argv", ["remote", "invalid"]), self.assertRaises(ValueError):
            main()

    def test_lease_is_started_before_remote_window_and_survives_timeout(self):
        with TemporaryDirectory() as tmp:
            config = SimpleNamespace(processing_root=Path(tmp))
            remote = SshMaintenance("odin", Path("/key"), Path("/entry"),
                                    {"unit": "logbook-graph-maintenance-" + "a" * 32 + ".service"})
            remote.recovered = Mock(return_value=False)
            remote.launch = Mock()
            child = Mock()
            child.stdin = io.StringIO()
            child.stdout = io.StringIO("READY\n")
            child.wait.side_effect = subprocess.TimeoutExpired([], 1)
            with patch("logbook.graph_maintenance_cross_host.subprocess.Popen", return_value=child), \
                 patch("logbook.graph_maintenance_cross_host.select.select", return_value=([child.stdout], [], [])):
                with self.assertRaisesRegex(TimeoutError, "independent lease"):
                    coordinate_leased_window(config, remote, timeout_seconds=1)
            remote.launch.assert_called_once()
            child.terminate.assert_not_called()

    def test_lease_refuses_old_or_invalid_window(self):
        with TemporaryDirectory() as tmp:
            config = SimpleNamespace(processing_root=Path(tmp))
            remote = SshMaintenance("odin", Path("/key"), Path("/entry"), {"unit": "old"})
            with self.assertRaisesRegex(ValueError, "fresh maintenance unit"):
                coordinate_leased_window(config, remote, timeout_seconds=1)

    def test_lease_requires_ready_and_confirmed_remote_result(self):
        with TemporaryDirectory() as tmp:
            config = SimpleNamespace(processing_root=Path(tmp))
            remote = SshMaintenance("odin", Path("/key"), Path("/entry"),
                                    {"unit": "logbook-graph-maintenance-" + "b" * 32 + ".service"})
            remote.recovered = Mock(return_value=True)
            with self.assertRaisesRegex(ValueError, "already reports recovery"):
                coordinate_leased_window(config, remote, timeout_seconds=1)
            remote.recovered = Mock(return_value=False)
            remote.launch = Mock()
            child = Mock(stdin=io.StringIO(), stdout=io.StringIO("BAD\n"))
            with patch("logbook.graph_maintenance_cross_host.subprocess.Popen", return_value=child), \
                 patch("logbook.graph_maintenance_cross_host.select.select", return_value=([child.stdout], [], [])):
                with self.assertRaisesRegex(RuntimeError, "lease did not start"):
                    coordinate_leased_window(config, remote, timeout_seconds=1)
            child.terminate.assert_called_once()
            remote.launch.assert_not_called()

    def test_lease_reports_success_failure_and_launch_errors(self):
        with TemporaryDirectory() as tmp:
            config = SimpleNamespace(processing_root=Path(tmp))
            remote = SshMaintenance("odin", Path("/key"), Path("/entry"),
                                    {"unit": "logbook-graph-maintenance-" + "c" * 32 + ".service"})
            remote.recovered = Mock(return_value=False)
            remote.launch = Mock()
            for status, error in ((0, None), (1, RuntimeError)):
                child = Mock(stdin=io.StringIO(), stdout=io.StringIO("READY\n"))
                child.wait.return_value = status
                with patch("logbook.graph_maintenance_cross_host.subprocess.Popen", return_value=child), \
                     patch("logbook.graph_maintenance_cross_host.select.select", return_value=([child.stdout], [], [])):
                    if error:
                        with self.assertRaises(error):
                            coordinate_leased_window(config, remote, timeout_seconds=1)
                    else:
                        coordinate_leased_window(config, remote, timeout_seconds=1)
            child = Mock(stdin=io.StringIO(), stdout=io.StringIO("READY\n"))
            child.wait.return_value = 0
            remote.launch.side_effect = ConnectionError("disconnected")
            with patch("logbook.graph_maintenance_cross_host.subprocess.Popen", return_value=child), \
                 patch("logbook.graph_maintenance_cross_host.select.select", return_value=([child.stdout], [], [])):
                with self.assertRaisesRegex(ConnectionError, "disconnected"):
                    coordinate_leased_window(config, remote, timeout_seconds=1)

    def test_lease_waits_through_errors_until_recovery(self):
        payload = '{"host":"odin","identity":"/key","entrypoint":"/entry","request":{}}\n'
        with patch("sys.argv", ["lease", "3", "4"]), patch("sys.stdin", io.StringIO(payload)), \
             patch("sys.stdout", new_callable=io.StringIO) as out, \
             patch("logbook.graph_maintenance_lease.os.fstat") as fstat, \
             patch("logbook.graph_maintenance_lease.time.sleep"), \
             patch("logbook.graph_maintenance_lease.SshMaintenance") as remote:
            remote.return_value.recovered.side_effect = [ConnectionError(), False, True]
            lease_main()
            self.assertEqual(out.getvalue(), "READY\n")
            self.assertEqual(fstat.call_count, 2)
        with patch("sys.argv", ["lease"]), self.assertRaises(ValueError):
            lease_main()
