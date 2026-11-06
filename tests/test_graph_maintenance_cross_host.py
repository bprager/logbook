import fcntl
import io
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from logbook.graph_maintenance_cross_host import SshMaintenance, coordinate_window, recovery_confirmed
from logbook.graph_maintenance_remote import main


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
