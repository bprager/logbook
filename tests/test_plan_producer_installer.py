import hashlib
import importlib.machinery
import importlib.util
import io
import os
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch


def installer_module():
    path = Path(__file__).resolve().parents[1] / "scripts/install-logbook-plan-producer"
    loader = importlib.machinery.SourceFileLoader("fixture_producer_installer", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def package(extra=None):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, value in [
            ("launcher", b"launcher"),
            ("logbook/__init__.py", b""),
            ("logbook/plan_producer.py", b"producer"),
            ("logbook/plan_attestation.py", b"proof"),
        ]:
            info = tarfile.TarInfo(name)
            info.size = len(value)
            archive.addfile(info, io.BytesIO(value))
        if extra is not None:
            info = tarfile.TarInfo(extra)
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
    return output.getvalue()


class ProducerInstallerTests(TestCase):
    def test_installs_checked_package_without_overwriting(self):
        module = installer_module()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "producer"
            launcher = root / "bin" / "producer"
            launcher.parent.mkdir()
            data = package()
            digest = hashlib.sha256(data).hexdigest()
            with patch.object(module, "trusted_ancestors"), patch.object(module.os, "chown"):
                module.install(data, digest, base, launcher, os.getuid(), os.getgid())
                self.assertEqual((base / "lib/logbook/plan_producer.py").read_bytes(), b"producer")
                self.assertEqual(launcher.read_bytes(), b"launcher")
                self.assertEqual((base / "private").stat().st_mode & 0o777, 0o700)
                with self.assertRaises(FileExistsError):
                    module.install(data, digest, base, launcher, os.getuid(), os.getgid())

    def test_installed_code_is_readable_with_restrictive_umask(self):
        module = installer_module()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "producer"
            launcher = root / "launcher"
            data = package()
            prior = os.umask(0o077)
            try:
                with patch.object(module, "trusted_ancestors"), patch.object(module.os, "chown"):
                    module.install(
                        data,
                        hashlib.sha256(data).hexdigest(),
                        base,
                        launcher,
                        os.getuid(),
                        os.getgid(),
                    )
            finally:
                os.umask(prior)
            self.assertEqual((base / "lib/logbook/plan_producer.py").stat().st_mode & 0o777, 0o644)
            self.assertEqual(launcher.stat().st_mode & 0o777, 0o755)
            self.assertEqual((base / "lib/logbook").stat().st_mode & 0o777, 0o755)

    def test_rejects_changed_and_unsafe_package_without_installation(self):
        module = installer_module()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            launcher = root / "launcher"
            base = root / "producer"
            for data, digest in [
                (package(), "0" * 64),
                (package("../escape"), None),
                (package("logbook/../../escape"), None),
            ]:
                with (
                    self.subTest(digest=digest),
                    patch.object(module, "trusted_ancestors"),
                    patch.object(module.os, "chown"),
                    self.assertRaises(ValueError),
                ):
                    module.install(
                        data,
                        digest or hashlib.sha256(data).hexdigest(),
                        base,
                        launcher,
                        os.getuid(),
                        os.getgid(),
                    )
                self.assertFalse(base.exists())
                self.assertFalse(launcher.exists())

    def test_rejects_symlink_and_duplicate_entries(self):
        module = installer_module()
        for variant in ("symlink", "duplicate"):
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode="w") as archive:
                info = tarfile.TarInfo("launcher")
                info.size = 1
                archive.addfile(info, io.BytesIO(b"x"))
                info = tarfile.TarInfo("logbook/file")
                if variant == "symlink":
                    info.type = tarfile.SYMTYPE
                    info.linkname = "/etc/passwd"
                    archive.addfile(info)
                else:
                    info.size = 1
                    archive.addfile(info, io.BytesIO(b"x"))
                    archive.addfile(info, io.BytesIO(b"y"))
            data = output.getvalue()
            with (
                TemporaryDirectory() as tmp,
                patch.object(module, "trusted_ancestors"),
                self.assertRaisesRegex(ValueError, "unsafe"),
            ):
                module.install(
                    data,
                    hashlib.sha256(data).hexdigest(),
                    Path(tmp) / "base",
                    Path(tmp) / "launcher",
                    os.getuid(),
                    os.getgid(),
                )

    def test_failed_install_removes_only_new_files(self):
        module = installer_module()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "producer"
            launcher = root / "launcher"
            data = package()
            keep = root / "unrelated"
            keep.write_text("preserved")
            with (
                patch.object(module, "trusted_ancestors"),
                patch.object(module.os, "chown", side_effect=OSError("simulated failure")),
                self.assertRaisesRegex(OSError, "simulated failure"),
            ):
                module.install(
                    data, hashlib.sha256(data).hexdigest(), base, launcher, os.getuid(), os.getgid()
                )
            self.assertFalse(base.exists())
            self.assertFalse(launcher.exists())
            self.assertEqual(keep.read_text(), "preserved")

    def test_builds_reproducible_package_from_exact_commit(self):
        import subprocess
        from logbook.operator_bundle import build_producer_bundle

        repo = Path(__file__).resolve().parents[1]
        revision = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        payload = build_producer_bundle(repo, revision)
        self.assertEqual(payload, build_producer_bundle(repo, revision))
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
            launcher = archive.extractfile("launcher").read()
            self.assertEqual(
                launcher,
                subprocess.check_output(
                    ["git", "-C", str(repo), "show", revision + ":scripts/logbook-plan-producer"]
                ),
            )
            self.assertIsNotNone(archive.extractfile("logbook/plan_producer.py"))
        with self.assertRaises(ValueError):
            build_producer_bundle(repo, "HEAD")

    def test_builder_rejects_noncommit_and_unsafe_archive(self):
        from types import SimpleNamespace

        from logbook.operator_bundle import build_producer_bundle

        revision = "a" * 40
        with patch(
            "logbook.operator_bundle.subprocess.run", return_value=SimpleNamespace(stdout=b"blob")
        ):
            with self.assertRaisesRegex(ValueError, "identify a commit"):
                build_producer_bundle(Path("."), revision)
        for name, kind, message in [
            ("src/logbook/link", tarfile.SYMTYPE, "unsupported file type"),
            ("other/file", tarfile.REGTYPE, "unexpected producer package path"),
        ]:
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode="w") as archive:
                info = tarfile.TarInfo(name)
                info.type = kind
                archive.addfile(info)
            results = [SimpleNamespace(stdout=b"commit"), SimpleNamespace(stdout=output.getvalue())]
            with (
                self.subTest(name=name),
                patch("logbook.operator_bundle.subprocess.run", side_effect=results),
            ):
                with self.assertRaisesRegex(ValueError, message):
                    build_producer_bundle(Path("."), revision)
