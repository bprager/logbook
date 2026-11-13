"""Standing maintenance delegation replaces per-version owner signatures."""

import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch


def bundle(version):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name in (
            "graph_maintenance_remote.py",
            "graph_maintenance_recovery.py",
            "graph_maintenance_worker.py",
            "named_repair_dispatcher.py",
            "operator_bundle_bootstrap.py",
        ):
            content = ("# fixture " + version + "\n").encode()
            member = tarfile.TarInfo("logbook/" + name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        content = ("# launcher " + version + "\n").encode()
        member = tarfile.TarInfo("logbook/_entrypoints/logbook-plan-producer")
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
    return stream.getvalue()


class MaintenanceAuthorityTests(TestCase):
    def test_schema_four_is_explicit_and_keeps_existing_record_limits(self):
        from logbook.operator_bundle_bootstrap import policy_plan_limit

        policy = {
            "schema": 4,
            "max_batch_records": 500,
            "max_plan_records": 5000,
            "expires_at": None,
            "maintenance": {"mode": "trusted-signed-releases", "release_key": "SHA256:" + "a" * 43},
        }
        self.assertEqual(policy_plan_limit(policy), 5000)
        for changed in (
            {"maintenance": {}},
            {"max_batch_records": 501},
            {"maintenance": {"mode": "all-code", "release_key": "SHA256:" + "a" * 43}},
        ):
            with self.assertRaises(ValueError):
                policy_plan_limit(policy | changed)

    def test_signed_routine_upgrade_needs_no_executor_owner_signature_and_rolls_back(self):
        from logbook.operator_bundle_bootstrap import (
            signed_policy,
            approved_release,
            verify_protected_executor,
        )
        from logbook.runtime_maintenance import install_executor_release
        from logbook.repair_release_publisher import publish_release

        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            guard = root / "guard"
            guard.mkdir(mode=0o700)
            owner = root / "owner"
            release_key = root / "release-key"
            for key in (owner, release_key):
                subprocess.run(
                    ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                    check=True,
                    capture_output=True,
                )
            blob = base64.b64decode(release_key.with_suffix(".pub").read_text().split()[1])
            fingerprint = "SHA256:" + base64.b64encode(
                hashlib.sha256(blob).digest()
            ).decode().rstrip("=")
            policy = {
                "schema": 4,
                "max_batch_records": 500,
                "max_plan_records": 5000,
                "expires_at": None,
                "maintenance": {"mode": "trusted-signed-releases", "release_key": fingerprint},
            }
            for name, content in [
                ("approval.allowed_signers", "owner " + owner.with_suffix(".pub").read_text()),
                (
                    "release.allowed_signers",
                    "release " + release_key.with_suffix(".pub").read_text(),
                ),
                ("standing-policy.json", json.dumps(policy)),
            ]:
                path = guard / name
                path.write_text(content)
                path.chmod(0o600)
            subprocess.run(
                [
                    "ssh-keygen",
                    "-Y",
                    "sign",
                    "-n",
                    "logbook-graph-policy",
                    "-f",
                    str(owner),
                    str(guard / "standing-policy.json"),
                ],
                check=True,
                capture_output=True,
            )
            (guard / "standing-policy.json.sig").chmod(0o600)
            # Invalid authority signatures and a different publisher cannot delegate updates.
            original_policy = (guard / "standing-policy.json").read_bytes()
            (guard / "standing-policy.json").write_bytes(original_policy + b" ")
            with self.assertRaisesRegex(ValueError, "signature invalid"):
                signed_policy(guard)
            (guard / "standing-policy.json").write_bytes(original_policy)
            signer = guard / "release.allowed_signers"
            original_signer = signer.read_bytes()
            signer.write_text("release " + owner.with_suffix(".pub").read_text())
            with self.assertRaisesRegex(ValueError, "identity does not match"):
                signed_policy(guard)
            signer.write_bytes(original_signer)
            owner.unlink()  # No private owner key exists for later routine releases.
            # Real consecutive public source generations must use actual signature
            # verification, not the mocked verifier in the filesystem-only test.
            from logbook import source_maintenance as source

            source_base = root / "source-base"
            source_base.mkdir(mode=0o755)
            source_guard = source_base / "maintenance"
            source_guard.mkdir(mode=0o700)
            for name in (
                "approval.allowed_signers",
                "release.allowed_signers",
                "standing-policy.json",
                "standing-policy.json.sig",
            ):
                path = source_guard / name
                path.write_bytes((guard / name).read_bytes())
                path.chmod(0o600)
            for number in (1, 2):
                data = bundle("source-" + str(number))
                directory = source_guard / "releases" / str(number)
                directory.parent.mkdir(mode=0o700, exist_ok=True)
                publish_release(data, "a" * 40, release_key, directory, sequence=number)
                for path in directory.iterdir():
                    path.chmod(0o600)
                (source_guard / "release.current").write_text(str(number) + "\n")
                (source_guard / "release.current").chmod(0o600)
                with (
                    patch("logbook.operator_bundle_bootstrap.trusted_parent"),
                    patch.object(source, "SOURCE_HELPERS", {}),
                ):
                    self.assertEqual(
                        source.install_source_release(data, source_guard, source_base), "UPDATED"
                    )
                    current = source.source_runtime(source_base)
                    self.assertEqual(current.stat().st_mode & 0o777, 0o755)
                    self.assertEqual((current / "release.json").stat().st_mode & 0o777, 0o644)
            public_manifest = current / "release.json"
            original_metadata = public_manifest.read_bytes()
            public_manifest.write_bytes(b"x" * 100001)
            with self.assertRaisesRegex(ValueError, "bounded installed source"):
                source.installed_source_release(source_guard, current)
            public_manifest.write_bytes(original_metadata)
            control = {p.name: (p.read_bytes(), p.stat().st_mode) for p in guard.iterdir()}
            helper = root / "helper"
            helper.write_bytes(b"initial")
            helper.chmod(0o700)
            for sequence, version in [(1, "one"), (2, "two"), (3, "three")]:
                data = bundle(version)
                out = guard / "releases" / str(sequence)
                out.parent.mkdir(mode=0o700, exist_ok=True)
                publish_release(data, "a" * 40, release_key, out, sequence=sequence)
                for p in out.iterdir():
                    p.chmod(0o600)
                (guard / "release.current").write_text(str(sequence) + "\n")
                (guard / "release.current").chmod(0o600)
                before = (guard / "executor/.bundle-ready").read_bytes() if sequence == 3 else None
                with (
                    patch("logbook.operator_bundle_bootstrap.trusted_parent"),
                    patch("logbook.runtime_maintenance.assert_quiet"),
                    patch(
                        "logbook.runtime_maintenance.HELPERS",
                        {helper: "named_repair_dispatcher.py"},
                    ),
                    patch("logbook.operator_bundle_bootstrap.PROTECTED_DISPATCHER", helper),
                ):
                    if sequence < 3:
                        if sequence == 2:
                            from logbook.operator_bundle_bootstrap import receive_release_request
                            from logbook.repair_release_delivery import accept_candidate

                            manifest = (out / "release.json").read_bytes()
                            signature = (out / "release.json.sig").read_bytes()

                            def transport(m, s, payload=None):
                                request = {"manifest": m.decode(), "signature": s.decode()}
                                if payload is not None:
                                    request["bundle"] = base64.b64encode(payload).decode()
                                return receive_release_request(guard, json.dumps(request).encode())

                            source = root / "source-delivery"
                            source.mkdir(mode=0o700)
                            with patch(
                                "logbook.repair_release_delivery.send_release",
                                side_effect=transport,
                            ):
                                accepted = accept_candidate(source, manifest, signature, data)
                            self.assertEqual(accepted["sequence"], 2)
                            self.assertEqual(transport(manifest, signature), "UNCHANGED")
                        else:
                            self.assertEqual(install_executor_release(data, guard), "UPDATED")
                        verify_protected_executor(guard)
                        self.assertEqual(install_executor_release(data, guard), "UNCHANGED")
                    else:
                        with self.assertRaisesRegex(ValueError, "digest mismatch"):
                            install_executor_release(b"bad package", guard)
                        bad_helper = root / "bad-helper"
                        bad_helper.write_bytes(b"untrusted")
                        bad_helper.chmod(0o666)
                        with patch(
                            "logbook.runtime_maintenance.HELPERS",
                            {bad_helper: "named_repair_dispatcher.py"},
                        ):
                            with self.assertRaisesRegex(ValueError, "trusted fixed"):
                                install_executor_release(data, guard)
                        with patch(
                            "logbook.runtime_maintenance.controls",
                            side_effect=[{}, {"changed": True}],
                        ):
                            with self.assertRaisesRegex(ValueError, "authority controls changed"):
                                install_executor_release(data, guard)
                        actual_approved = approved_release

                        def stale(g, directory=None):
                            item = actual_approved(g, directory)
                            return item if directory is not None else item | {"sequence": 1}

                        with patch(
                            "logbook.operator_bundle_bootstrap.approved_release", side_effect=stale
                        ):
                            with self.assertRaisesRegex(ValueError, "stale or conflicts"):
                                install_executor_release(data, guard)
                        with patch(
                            "logbook.runtime_maintenance.validate_executor",
                            side_effect=RuntimeError("injected validation failure"),
                        ):
                            with self.assertRaisesRegex(RuntimeError, "injected"):
                                install_executor_release(data, guard)
                        self.assertEqual((guard / "executor/.bundle-ready").read_bytes(), before)
                        self.assertEqual(approved_release(guard)["sequence"], 3)
                with patch(
                    "logbook.operator_bundle_bootstrap.signed_policy", return_value={"schema": 3}
                ):
                    with self.assertRaisesRegex(ValueError, "delegation required"):
                        install_executor_release(data, guard)
                self.assertEqual(signed_policy(guard), policy)
                self.assertFalse((guard / "executor-approval.json").exists())
                for name, (content, mode) in control.items():
                    self.assertEqual((guard / name).read_bytes(), content)
                    self.assertEqual((guard / name).stat().st_mode, mode)

    def test_source_generations_update_without_touching_running_generation(self):
        from logbook import source_maintenance as source
        from logbook.runtime_maintenance import atomic_file

        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            base = root / "base"
            base.mkdir(mode=0o755)
            guard = base / "maintenance"
            guard.mkdir(mode=0o700)
            control = guard / "standing-policy.json"
            control.write_text("signed owner authority")
            control.chmod(0o600)
            helper = root / "launcher"
            helper.write_text("# original launcher\n")
            helper.chmod(0o755)

            for sequence, version in [(1, "first"), (2, "second"), (3, "failure")]:
                data = bundle(version)
                release = {
                    "schema": 2,
                    "sequence": sequence,
                    "revision": "b" * 40,
                    "bundle_sha256": hashlib.sha256(data).hexdigest(),
                }
                for name, content in [
                    ("release.json", json.dumps(release)),
                    ("release.json.sig", "fixture"),
                ]:
                    path = guard / name
                    path.write_text(content)
                    path.chmod(0o600)
                prior = (base / "source.current").read_bytes() if sequence > 1 else None
                with (
                    patch.object(source.bootstrap, "signed_policy", return_value={"schema": 4}),
                    patch.object(
                        source.bootstrap,
                        "approved_release",
                        side_effect=lambda g, directory=None: (
                            release
                            if directory is None
                            else json.loads((directory / "release.json").read_text())
                        ),
                    ),
                    patch.object(source.bootstrap, "trusted_parent"),
                    patch.object(source, "SOURCE_HELPERS", {helper: "logbook-plan-producer"}),
                ):
                    if sequence < 3:
                        if sequence == 1:
                            real_atomic = source.atomic_file

                            def reject_receipt(path, content, mode, owner=None):
                                if path.name.startswith("source-maintenance-"):
                                    raise OSError("first activation receipt failed")
                                return real_atomic(path, content, mode, owner)

                            with patch.object(source, "atomic_file", side_effect=reject_receipt):
                                with self.assertRaisesRegex(OSError, "first activation"):
                                    source.install_source_release(data, guard, base)
                            self.assertFalse((base / "source.current").exists())
                        self.assertEqual(
                            source.install_source_release(data, guard, base), "UPDATED"
                        )
                        selected = source.source_runtime(base)
                        self.assertEqual(selected.name, release["bundle_sha256"])
                        self.assertEqual(
                            source.install_source_release(data, guard, base), "UNCHANGED"
                        )
                        if sequence == 1:
                            first = selected
                        else:
                            self.assertTrue(first.exists())
                    else:
                        with self.assertRaisesRegex(ValueError, "digest mismatch"):
                            source.install_source_release(b"wrong", guard, base)
                        release["sequence"] = 1
                        with self.assertRaisesRegex(ValueError, "stale or conflicts"):
                            source.install_source_release(data, guard, base)
                        release["sequence"] = 3
                        current_metadata = source.source_runtime(base) / "release.json"
                        saved_metadata = current_metadata.read_bytes()
                        current_metadata.write_text(
                            json.dumps(json.loads(saved_metadata) | {"bundle_sha256": "0" * 64})
                        )
                        with self.assertRaisesRegex(ValueError, "does not match generation"):
                            source.install_source_release(data, guard, base)
                        current_metadata.write_bytes(saved_metadata)
                        with patch.object(source, "controls", side_effect=[{}, {"changed": True}]):
                            with self.assertRaisesRegex(ValueError, "controls changed"):
                                source.install_source_release(data, guard, base)
                        helper.chmod(0o666)
                        with self.assertRaisesRegex(ValueError, "trusted fixed source"):
                            source.install_source_release(data, guard, base)
                        helper.chmod(0o755)

                        def fail_receipt(path, content, mode, owner=None):
                            if path.name.startswith("source-maintenance-"):
                                raise OSError("receipt write failed")
                            return atomic_file(path, content, mode, owner)

                        with patch.object(source, "atomic_file", side_effect=fail_receipt):
                            with self.assertRaisesRegex(OSError, "receipt write failed"):
                                source.install_source_release(data, guard, base)
                        self.assertEqual((base / "source.current").read_bytes(), prior)
            with patch.object(source.bootstrap, "signed_policy", return_value={"schema": 3}):
                with self.assertRaisesRegex(ValueError, "delegation required"):
                    source.install_source_release(data, guard, base)
            invalid = root / "invalid-generation"
            invalid.mkdir()
            (invalid / ".bundle-ready").write_text("wrong")
            with self.assertRaisesRegex(ValueError, "generation mismatch"):
                source.validate_source_generation(invalid, "a" * 64)
            (invalid / "link").symlink_to(control)
            with self.assertRaisesRegex(ValueError, "untrusted source"):
                source.validate_source_generation(invalid, "a" * 64)
            self.assertEqual(control.read_text(), "signed owner authority")
            self.assertEqual(helper.read_text(), "# launcher second\n")
            pointer = base / "source.current"
            pointer.write_text("../outside")
            with self.assertRaises(ValueError):
                source.source_runtime(base)
            pointer.chmod(0o666)
            with self.assertRaises(ValueError):
                source.source_runtime(base)

    def test_busy_maintenance_cannot_be_replaced(self):
        from types import SimpleNamespace
        from logbook.runtime_maintenance import assert_quiet

        safe = [
            "",
            "ActiveState=inactive\nMainPID=0\n",
            "ActiveState=failed\nMainPID=0\n",
            "ActiveState=active\nSubState=exited\nResult=success\nRestart=no\n",
            "Id=window.timer\nActiveState=active\nNextElapseUSecMonotonic=infinity\n",
            "ActiveState=active\nSubState=exited\nResult=success\nRestart=on-failure\n",
        ]
        unsafe = [
            "ActiveState=active\nMainPID=123\n",
            "ActiveState=activating\nMainPID=0\n",
            "Id=window.timer\nActiveState=active\nNextElapseUSecMonotonic=123\n",
        ]
        for value in safe + unsafe:
            with patch(
                "logbook.runtime_maintenance.subprocess.run",
                return_value=SimpleNamespace(stdout=value),
            ):
                if value in unsafe:
                    with self.assertRaises(ValueError):
                        assert_quiet()
                else:
                    assert_quiet()


class MaintenanceBoundaryTests(TestCase):
    def test_dispatch_and_install_locks_serialize_real_files(self):
        from logbook import named_repair_dispatcher as dispatcher
        from logbook.runtime_maintenance import runtime_lock

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(dispatcher, "GUARD", root):
                with dispatcher.dispatch_lock():
                    with self.assertRaises(BlockingIOError):
                        with runtime_lock(root):
                            self.fail("exclusive installer crossed dispatcher lock")
                with runtime_lock(root):
                    pass
                lock = root / "runtime-update.lock"
                lock.chmod(0o644)
                for acquire in (dispatcher.dispatch_lock, lambda: runtime_lock(root)):
                    with self.assertRaisesRegex(ValueError, "private runtime"):
                        with acquire():
                            pass

    def test_sender_transfers_bounded_package_without_owner_credentials(self):
        from types import SimpleNamespace
        from logbook.repair_release_delivery import send_release

        with patch(
            "logbook.repair_release_delivery.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout="UPDATED\n"),
        ) as run:
            self.assertEqual(send_release(b"{}", b"signature", b"package"), "UPDATED")
            request = json.loads(run.call_args.kwargs["input"])
            self.assertEqual(base64.b64decode(request["bundle"]), b"package")
            self.assertIn("BatchMode=yes", run.call_args.args[0])
            with self.assertRaisesRegex(ValueError, "bounded maintenance bundle"):
                send_release(b"{}", b"signature", b"")
        from logbook.operator_bundle_bootstrap import receive_release_request, receive_release

        with patch("logbook.operator_bundle_bootstrap.signed_policy", return_value={"schema": 3}):
            with self.assertRaisesRegex(ValueError, "delegation required"):
                receive_release_request(
                    Path("/guard"),
                    json.dumps({"manifest": "{}", "signature": "sig", "bundle": ""}).encode(),
                )
            with self.assertRaisesRegex(ValueError, "fixed source"):
                receive_release(Path("/wrong-source"), b"{}", b"sig", _source=True)
        from logbook.repair_release_delivery import accept_candidate

        with TemporaryDirectory() as temporary:
            data = b"bundle"
            manifest = json.dumps(
                {
                    "schema": 2,
                    "sequence": 1,
                    "revision": "a" * 40,
                    "bundle_sha256": hashlib.sha256(data).hexdigest(),
                }
            ).encode()
            with patch(
                "logbook.repair_release_delivery.send_release",
                side_effect=["MAINTENANCE_REQUIRED", "REJECTED"],
            ):
                with self.assertRaisesRegex(ConnectionError, "not accepted"):
                    accept_candidate(Path(temporary), manifest, b"sig", data)
