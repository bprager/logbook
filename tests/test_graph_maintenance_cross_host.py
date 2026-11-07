import fcntl
import io
import json
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
            remote.preflight = Mock()
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
            remote.preflight = Mock()
            remote.recovered = Mock(return_value=True)
            with self.assertRaisesRegex(ValueError, "already reports recovery"):
                coordinate_leased_window(config, remote, timeout_seconds=1)
            remote.preflight = Mock()
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
            remote.preflight = Mock()
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

class MaintenancePreflightTests(unittest.TestCase):
    def test_rejected_preflight_never_starts_lock_lease_or_remote_worker(self):
        with TemporaryDirectory() as tmp:
            config = SimpleNamespace(processing_root=Path(tmp))
            remote = SshMaintenance("odin", Path("/key"), Path("/entry"),
                                    {"unit": "logbook-graph-maintenance-" + "d" * 32 + ".service"})
            remote.preflight = Mock(side_effect=ValueError("untrusted worker"))
            remote.recovered = Mock(return_value=False)
            remote.launch = Mock()
            with patch("logbook.graph_maintenance_cross_host.subprocess.Popen") as spawn:
                with self.assertRaisesRegex(ValueError, "untrusted worker"):
                    coordinate_leased_window(config, remote, timeout_seconds=1)
            spawn.assert_not_called()
            remote.launch.assert_not_called()

    def test_remote_preflight_is_read_only(self):
        request = '{"container_id":"id","pid":1,"port":2,"unit":"unit",' \
                  '"private_dir":"/private","worker":"/worker",' \
                  '"recovery_file":"/recovery","seconds":10}'
        with patch("logbook.graph_maintenance_remote.DockerRecovery"), \
             patch("logbook.graph_maintenance_remote.DockerLaunch") as launcher, \
             patch("sys.stdin", io.StringIO(request)), patch("sys.stdout", new_callable=io.StringIO) as out, \
             patch("sys.argv", ["remote", "preflight"]):
            main()
            launcher.return_value.preflight.assert_called_once()
            launcher.return_value.run_window.assert_not_called()
            self.assertEqual(out.getvalue().strip(), "PREFLIGHT_OK")

    def test_preflight_ssh_requires_explicit_success(self):
        remote = SshMaintenance("odin", Path("/key"), Path("/entry"), {})
        with patch.object(remote, "invoke", return_value="PREFLIGHT_OK"):
            remote.preflight()
        with patch.object(remote, "invoke", return_value="PENDING"):
            with self.assertRaises(ConnectionError):
                remote.preflight()

class RejectedLaunchTests(unittest.TestCase):
    def test_explicit_prestart_rejection_stops_lease_and_releases_locks(self):
        from logbook.graph_maintenance_cross_host import StartupRejected
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = SshMaintenance("odin", Path("/key"), Path("/entry"),
                                    {"unit": "logbook-graph-maintenance-" + "e" * 32 + ".service"})
            remote.preflight = Mock()
            remote.recovered = Mock(return_value=False)
            remote.launch = Mock(side_effect=StartupRejected("rejected"))
            child = Mock(stdin=io.StringIO(), stdout=io.StringIO("READY\n"))
            child.wait.return_value = -15
            with patch("logbook.graph_maintenance_cross_host.subprocess.Popen", return_value=child), \
                 patch("logbook.graph_maintenance_cross_host.select.select", return_value=([child.stdout], [], [])):
                with self.assertRaises(StartupRejected):
                    coordinate_leased_window(SimpleNamespace(processing_root=root), remote, timeout_seconds=1)
            child.terminate.assert_called_once()
            with (root / "pipeline.lock").open("a") as contender:
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_remote_prestart_rejection_is_distinct_from_connection_failure(self):
        from logbook.graph_maintenance_cross_host import StartupRejected
        remote = SshMaintenance("odin", Path("/key"), Path("/entry"), {})
        with patch.object(remote, "invoke", return_value="REJECTED"):
            with self.assertRaises(StartupRejected):
                remote.launch()

    def test_remote_reports_only_prestart_rejection_as_safe(self):
        from logbook.graph_maintenance_cross_host import StartupRejected
        request = '{"container_id":"id","pid":1,"port":2,"unit":"unit",' \
                  '"private_dir":"/private","worker":"/worker",' \
                  '"recovery_file":"/recovery","seconds":10}'
        for error, expected in ((StartupRejected("before"), "REJECTED\n"),
                                (RuntimeError("after"), None)):
            with patch("logbook.graph_maintenance_remote.DockerRecovery"), \
                 patch("logbook.graph_maintenance_remote.DockerLaunch") as launcher, \
                 patch("sys.stdin", io.StringIO(request)), patch("sys.stdout", new_callable=io.StringIO) as out, \
                 patch("sys.argv", ["remote", "launch"]):
                launcher.return_value.run_window.side_effect = error
                if expected is None:
                    with self.assertRaisesRegex(RuntimeError, "after"):
                        main()
                    self.assertEqual(out.getvalue(), "")
                else:
                    main()
                    self.assertEqual(out.getvalue(), expected)

    def test_retry_response_must_confirm_start(self):
        remote = SshMaintenance('odin', Path('/key'), Path('/entry'), {})
        with patch.object(remote, 'invoke', return_value='RETRY_STARTED'):
            remote.retry()
        with patch.object(remote, 'invoke', return_value='PENDING'):
            with self.assertRaises(ConnectionError):
                remote.retry()

    def test_remote_retry_calls_launcher(self):
        request = '{"container_id":"id","pid":1,"port":2,"unit":"unit",' \
                  '"private_dir":"/private","worker":"/worker",' \
                  '"recovery_file":"/recovery","seconds":10}'
        with patch('logbook.graph_maintenance_remote.DockerRecovery'), \
             patch('logbook.graph_maintenance_remote.DockerLaunch') as launcher, \
             patch('sys.stdin', io.StringIO(request)), patch('sys.stdout', new_callable=io.StringIO) as out, \
             patch('sys.argv', ['remote', 'retry']):
            main()
            launcher.return_value.retry_recovery.assert_called_once()
            self.assertEqual(out.getvalue(), 'RETRY_STARTED\n')

    def test_remote_staging_response_and_target_validation(self):
        remote = SshMaintenance('odin', Path('/key'), Path('/entry'), {'unit':'fixture'})
        with patch.object(remote, 'invoke', return_value='STAGED') as invoke:
            remote.stage({'nodes':[]})
            self.assertEqual(invoke.call_args.args[1]['plan_data'], {'nodes':[]})
        with patch.object(remote, 'invoke', return_value='NO'):
            with self.assertRaises(ConnectionError):
                remote.stage({})
        request = '{"container_id":"id","pid":1,"port":2,"unit":"unit",' \
                  '"private_dir":"/private","worker":"/worker",' \
                  '"recovery_file":"/recovery","seconds":10}'
        with patch('logbook.graph_maintenance_remote.DockerRecovery'), \
             patch('logbook.graph_maintenance_remote.DockerLaunch') as launcher, \
             patch('logbook.graph_maintenance_remote.stage_plan') as stage, \
             patch('sys.stdin', io.StringIO(request)), patch('sys.stdout', new_callable=io.StringIO) as out, \
             patch('sys.argv', ['remote', 'stage']):
            main()
            launcher.return_value.verify_target.assert_called_once()
            stage.assert_called_once()
            self.assertEqual(out.getvalue(), 'STAGED\n')

    def test_outcome_requires_successful_watchdog_and_matching_records(self):
        request = {'container_id':'a'*64, 'pid':1, 'port':7687,
                   'unit':'logbook-graph-maintenance-test.service', 'private_dir':'/private',
                   'worker':'/worker', 'recovery_file':'/recovery', 'seconds':10, 'journal':'/private/journal'}
        batch = '00000000-0000-0000-0000-000000000001'
        record = {'schema':1, 'batch':batch}
        outcome = {key:request[key] for key in ('container_id','pid','port','unit')}
        outcome.update({'batch':batch, 'outcome':'retained'})
        with patch('logbook.graph_maintenance_remote.DockerRecovery'), \
             patch('logbook.graph_maintenance_remote.DockerLaunch'), \
             patch('logbook.graph_maintenance_remote.recovery_confirmed', side_effect=[False, True, True]), \
             patch('logbook.graph_maintenance_remote.read_private_json', side_effect=[record, outcome, record, {**outcome, 'batch':'other'}]), \
             patch('logbook.graph_maintenance_remote.completion_matches', return_value=True), \
             patch('sys.stdout', new_callable=io.StringIO) as out, \
             patch('sys.argv', ['remote', 'outcome']):
            with patch('sys.stdin', io.StringIO(json.dumps(request))):
                main()
            with patch('sys.stdin', io.StringIO(json.dumps(request))):
                main()
            with patch('sys.stdin', io.StringIO(json.dumps(request))), self.assertRaisesRegex(ValueError, 'outcome'):
                main()
            self.assertEqual(out.getvalue(), 'PENDING\nretained\n')

    def test_ssh_outcome_refuses_unconfirmed_result(self):
        remote = SshMaintenance('odin', Path('/key'), Path('/entry'), {})
        with patch.object(remote, 'invoke', return_value='not-retained'):
            self.assertEqual(remote.outcome(), 'not-retained')
        with patch.object(remote, 'invoke', return_value='PENDING'):
            with self.assertRaisesRegex(RuntimeError, 'not confirmed'):
                remote.outcome()

    def test_outcome_rejects_invalid_journal_and_conflicting_completion(self):
        request = {'container_id':'a'*64, 'pid':1, 'port':7687, 'unit':'unit',
                   'private_dir':'/private', 'worker':'/worker', 'recovery_file':'/recovery',
                   'seconds':10, 'journal':'/private/journal'}
        batch = '00000000-0000-0000-0000-000000000001'
        base = {key:request[key] for key in ('container_id','pid','port','unit')}
        base['batch'] = batch
        cases = [([{'schema':0,'batch':batch}], False, False, 'journal'),
                 ([{'schema':1,'batch':batch}, {**base,'outcome':'retained'}], False, False, 'completion'),
                 ([{'schema':1,'batch':batch}, {**base,'outcome':'rolled-back'}], False, True, 'receipt')]
        for values, matches, exists, message in cases:
            with patch('logbook.graph_maintenance_remote.DockerRecovery'), \
                 patch('logbook.graph_maintenance_remote.DockerLaunch'), \
                 patch('logbook.graph_maintenance_remote.recovery_confirmed', return_value=True), \
                 patch('logbook.graph_maintenance_remote.read_private_json', side_effect=values), \
                 patch('logbook.graph_maintenance_remote.completion_matches', return_value=matches), \
                 patch('logbook.graph_maintenance_remote.os.path.lexists', return_value=exists), \
                 patch('sys.stdin', io.StringIO(json.dumps(request))), \
                 patch('sys.argv', ['remote', 'outcome']), self.assertRaisesRegex(ValueError, message):
                main()

    def test_fixture_only_staging_refuses_other_containers(self):
        request = '{"container_id":"id","pid":1,"port":2,"unit":"unit",' \
                  '"private_dir":"/private","worker":"/worker",' \
                  '"recovery_file":"/recovery","seconds":10,"sandbox_only":true}'
        with patch('logbook.graph_maintenance_remote.DockerRecovery') as recovery, \
             patch('logbook.graph_maintenance_remote.DockerLaunch'), \
             patch('logbook.graph_maintenance_remote.stage_plan') as stage, \
             patch('sys.stdin', io.StringIO(request)), patch('sys.argv', ['remote', 'stage']):
            recovery.return_value.run.return_value = '/memgraph-service'
            with self.assertRaisesRegex(ValueError, 'fixture'):
                main()
            stage.assert_not_called()

    def test_fixture_only_retry_refuses_other_containers(self):
        request = '{"container_id":"id","pid":1,"port":2,"unit":"unit",' \
                  '"private_dir":"/private","worker":"/worker",' \
                  '"recovery_file":"/recovery","seconds":10,"sandbox_only":true}'
        with patch('logbook.graph_maintenance_remote.DockerRecovery') as recovery, \
             patch('logbook.graph_maintenance_remote.DockerLaunch') as launcher, \
             patch('sys.stdin', io.StringIO(request)), patch('sys.argv', ['remote', 'retry']):
            recovery.return_value.run.return_value = '/memgraph-service'
            with self.assertRaisesRegex(ValueError, 'fixture'):
                main()
            launcher.return_value.retry_recovery.assert_not_called()

    def test_fixture_guard_is_removed_before_staging(self):
        request = {'container_id':'id', 'pid':1, 'port':2, 'unit':'unit',
                   'private_dir':'/private', 'worker':'/worker', 'recovery_file':'/recovery',
                   'seconds':10, 'sandbox_only':True, 'boot_guard_dir':'/guard',
                   'boot_guard_service':'logbook-fixture-graph.service'}
        events = []
        with patch('logbook.graph_maintenance_remote.DockerRecovery') as recovery, \
             patch('logbook.graph_maintenance_remote.DockerLaunch'), \
             patch('logbook.graph_maintenance_remote.verify_fixture_binding'), \
             patch('logbook.graph_maintenance_remote.FixtureBootGuard') as guard, \
             patch('logbook.graph_maintenance_remote.stage_plan', side_effect=lambda *_:events.append('stage')), \
             patch('sys.stdin', io.StringIO(json.dumps(request))), \
             patch('sys.stdout', new_callable=io.StringIO), patch('sys.argv', ['remote', 'stage']):
            recovery.return_value.run.return_value = '/logbook-fixture-graph'
            guard.return_value.disarm.side_effect = lambda:events.append('guard')
            main()
            self.assertEqual(events, ['guard', 'stage'])

    def test_fixture_guard_restored_only_after_confirmed_outcome(self):
        request = {'container_id':'a'*64, 'pid':1, 'port':7687, 'unit':'unit',
                   'private_dir':'/private', 'worker':'/worker', 'recovery_file':'/recovery',
                   'seconds':10, 'journal':'/private/journal', 'sandbox_only':True,
                   'boot_guard_dir':'/guard', 'boot_guard_service':'logbook-fixture-graph.service'}
        batch = '00000000-0000-0000-0000-000000000001'
        record = {'schema':1, 'batch':batch}
        outcome = {key:request[key] for key in ('container_id','pid','port','unit')}
        outcome.update({'batch':batch, 'outcome':'retained'})
        with patch('logbook.graph_maintenance_remote.DockerRecovery'), \
             patch('logbook.graph_maintenance_remote.DockerLaunch'), \
             patch('logbook.graph_maintenance_remote.verify_fixture_binding'), \
             patch('logbook.graph_maintenance_remote.FixtureBootGuard') as guard, \
             patch('logbook.graph_maintenance_remote.recovery_confirmed', side_effect=[False,True]), \
             patch('logbook.graph_maintenance_remote.read_private_json', side_effect=[record,outcome]), \
             patch('logbook.graph_maintenance_remote.completion_matches', return_value=True), \
             patch('sys.stdout', new_callable=io.StringIO), patch('sys.argv', ['remote', 'outcome']):
            with patch('sys.stdin', io.StringIO(json.dumps(request))):
                main()
            guard.assert_not_called()
            with patch('sys.stdin', io.StringIO(json.dumps(request))):
                main()
            guard.return_value.allow.assert_called_once()
