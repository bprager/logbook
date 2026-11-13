import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch


class AutomaticColdRecoveryTests(TestCase):
    def test_saved_pending_recovery_uses_existing_machine_route_and_receipts(self):
        from logbook.repair_cycle import repair_cycle
        from logbook.lock_guard_client import OPERATOR_KEY

        for accepted in (True, False):
            with TemporaryDirectory() as temporary:
                root = Path(temporary)
                state = root / ("repair-" + "a" * 32 + ".json")
                state.write_text("{}")
                state.chmod(0o600)
                pending = root / "repair-pending.json"
                pending.write_text(json.dumps({"phase": "running", "state": state.name}))
                pending.chmod(0o600)
                remote = Mock(host="logbook-repair@odin", identity=OPERATOR_KEY)
                remote.invoke.side_effect = ["OFFLINE_REQUIRED", "retained", "STARTED"]
                remote.recovered.return_value = accepted
                remote.outcome.return_value = "retained"
                with (
                    patch("logbook.repair_cycle._named_remote", return_value=remote),
                    patch("logbook.repair_cycle.release_named_handoff") as release,
                ):
                    self.assertEqual(
                        repair_cycle(root), "retained" if accepted else "recovery-pending"
                    )
                    self.assertEqual(
                        [call.args[0] for call in remote.invoke.call_args_list],
                        ["named-status", "named-offline", "named-authorize"],
                    )
                    self.assertEqual(release.called, accepted)
                    self.assertEqual(pending.exists(), not accepted)

    def test_offline_selector_requires_matching_target_changed_boot_and_closed_guard(self):
        from logbook import fixture_offline_recovery as offline

        self.assertTrue(hasattr(offline, "named_offline_required"))
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = {
                "private_dir": str(root),
                "container_id": "a" * 64,
                "pid": 42,
                "port": 7697,
                "unit": "logbook-graph-maintenance-" + "b" * 32 + ".service",
                "boot_guard_service": "memgraph.service",
                "boot_guard_dir": str(root),
                "journal": str(root / "journal"),
                "recovery_file": "/fixed/recovery.py",
            }
            record = {
                key: request[field]
                for key, field in {
                    "container": "container_id",
                    "pid": "pid",
                    "unit": "unit",
                    "service": "boot_guard_service",
                    "guard_dir": "boot_guard_dir",
                    "journal": "journal",
                    "port": "port",
                    "recovery_file": "recovery_file",
                }.items()
            }
            record["boot_id"] = "old-boot"
            saved = root / "boot-target.json"
            saved.write_text(json.dumps(record))
            saved.chmod(0o600)
            with (
                patch.object(offline, "_validate_named"),
                patch.object(offline, "boot_id", return_value="new-boot"),
                patch.object(offline, "MemgraphBootGuard") as guard,
            ):
                guard.return_value.marker = root / "start-allowed"
                self.assertTrue(offline.named_offline_required(request, Mock()))
                guard.return_value.verify.assert_called_with("inactive")
                for changed in ({"boot_id": "new-boot"}, {"pid": 43}):
                    saved.write_text(json.dumps(record | changed))
                    self.assertFalse(offline.named_offline_required(request, Mock()))
                saved.write_text(json.dumps(record))
                (root / "start-allowed").touch()
                self.assertFalse(offline.named_offline_required(request, Mock()))
                with patch(
                    "logbook.fixture_recovery_release.verify_named_graph_receipt",
                    return_value={"outcome": "retained"},
                ):
                    self.assertTrue(offline.named_offline_required(request, Mock()))
                    guard.return_value._check_marker.assert_called()

    def test_private_transport_failure_exposes_actual_command_and_both_streams(self):
        from logbook.graph_maintenance_cross_host import SshMaintenance

        remote = SshMaintenance("odin", Path("/key"), Path("/entry"), {})
        with patch(
            "logbook.graph_maintenance_cross_host.subprocess.run",
            return_value=SimpleNamespace(
                returncode=1, stdout="actual stdout", stderr="actual stderr"
            ),
        ):
            with self.assertRaises(ConnectionError) as raised:
                remote.invoke("status")
        self.assertIn("actual stdout", str(raised.exception))
        self.assertIn("actual stderr", str(raised.exception))
        self.assertIn("ssh -i /key", str(raised.exception))

    def test_live_startup_evidence_survives_process_disappearance(self):
        from logbook import fixture_offline_recovery as offline

        self.assertTrue(hasattr(offline, "StartupEvidence"))
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            sample = {"alive": True, "logs": {"memgraph.log": "loading snapshot"}, "memory": "4096"}
            with (
                patch("logbook.recovery_startup_evidence.capture", return_value=sample),
                patch("sys.stderr", new_callable=io.StringIO) as error,
            ):
                with self.assertRaisesRegex(RuntimeError, "original failure"):
                    with offline.StartupEvidence("123", root) as evidence:
                        evidence.sample()
                        raise RuntimeError("original failure")
            saved = list(root.glob("offline-startup-*.json"))
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0].stat().st_mode & 0o777, 0o600)
            self.assertIn("loading snapshot", saved[0].read_text())
            self.assertIn("loading snapshot", error.getvalue())

    def test_dead_process_polls_preserve_the_last_living_evidence(self):
        from logbook.recovery_startup_evidence import StartupEvidence

        with TemporaryDirectory() as temporary:
            living = {"alive": True, "logs": {"startup": "actual last startup stage"}}
            dead = {"alive": False, "logs": {}}
            with patch(
                "logbook.recovery_startup_evidence.capture", side_effect=[living] + [dead] * 10
            ):
                evidence = StartupEvidence("123", Path(temporary))
                for _ in range(11):
                    evidence.sample()
            self.assertIn(living, evidence.samples)
            self.assertEqual(evidence.samples[-1], dead)
            self.assertLessEqual(len(evidence.samples), 6)

    def test_capture_reads_real_bounded_files_without_following_log_links(self):
        from logbook.recovery_startup_evidence import capture

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            process = root / "123"
            logs = process / "root/var/log/memgraph"
            logs.mkdir(parents=True)
            (process / "status").write_text("VmRSS: 1024 kB")
            (logs / "memgraph.log").write_text("old" * 3000 + "snapshot loading")
            (logs / "link").symlink_to(process / "status")
            frame = capture("123", root)
            self.assertTrue(frame["alive"])
            self.assertEqual(frame["status"], "VmRSS: 1024 kB")
            self.assertTrue(frame["logs"]["memgraph.log"].endswith("snapshot loading"))
            self.assertEqual(len(frame["logs"]["memgraph.log"]), 4096)
            self.assertNotEqual(frame["logs"]["link"], "VmRSS: 1024 kB")
            self.assertFalse(capture("456", root)["alive"])
            with self.assertRaisesRegex(ValueError, "positive"):
                capture("0", root)

    def test_collector_polls_and_keeps_original_failure_if_record_write_fails(self):
        import threading
        from logbook.recovery_startup_evidence import StartupEvidence

        observed = threading.Event()
        count = 0

        def sample(pid):
            nonlocal count
            count += 1
            if count > 1:
                observed.set()
            return {"alive": False, "logs": {}, "sample": count}

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch("logbook.recovery_startup_evidence.capture", side_effect=sample):
                with StartupEvidence("123", root):
                    self.assertTrue(observed.wait(3))
                with patch("sys.stderr", new_callable=io.StringIO) as error:
                    with self.assertRaisesRegex(RuntimeError, "worker failed"):
                        with StartupEvidence("123", root / "missing"):
                            raise RuntimeError("worker failed")
                self.assertIn("startup_evidence_write_error", error.getvalue())

    def test_remote_reports_offline_required_and_reuses_verified_receipt(self):
        from logbook.graph_maintenance_remote import main

        request = {
            "container_id": "id",
            "pid": 1,
            "port": 7697,
            "unit": "unit",
            "private_dir": "/private",
            "worker": "/worker",
            "recovery_file": "/recovery",
            "seconds": 10,
            "profile": "named-graph",
            "boot_guard_service": "memgraph.service",
            "boot_guard_dir": "/var/lib/logbook-graph-guard",
        }
        with (
            patch("logbook.graph_maintenance_remote.DockerRecovery"),
            patch("logbook.graph_maintenance_remote.DockerLaunch") as launcher,
            patch("logbook.graph_maintenance_remote.recovery_confirmed", return_value=False),
            patch("logbook.graph_maintenance_remote.fixture_restarted", return_value=False),
            patch("logbook.graph_maintenance_remote.named_offline_required", return_value=True),
            patch(
                "logbook.graph_maintenance_remote.verify_named_graph_receipt",
                return_value={"outcome": "retained"},
            ),
            patch("logbook.graph_maintenance_remote.recover_named_graph_offline") as recover,
            patch("sys.stdout", new_callable=io.StringIO) as out,
        ):
            launcher.return_value.cancelled.return_value = False
            for action in ("named-status", "named-offline"):
                with (
                    patch("sys.argv", ["remote", action]),
                    patch("sys.stdin", io.StringIO(json.dumps(request))),
                ):
                    main()
            self.assertEqual(out.getvalue(), "OFFLINE_REQUIRED\nretained\n")
            recover.assert_not_called()
