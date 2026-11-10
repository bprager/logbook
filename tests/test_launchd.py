import plistlib
import io
import tempfile
import subprocess
from dataclasses import replace
from contextlib import redirect_stdout
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from logbook.cli import main
from logbook.config import load_app_config
from logbook.launchd import render_launchd_package, write_launchd_package, _write_signed_app_bundle


class LaunchdPackagingTests(TestCase):
    def test_missing_identity_marker_cannot_downgrade_certificate_signed_app(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = render_launchd_package(
                config=load_app_config(_write_env(root)), env_path=root / '.env',
                output_dir=root / 'generated', repo_root=root,
            )
            package.mount_runner.bundle_path.mkdir(parents=True)
            with patch('logbook.launchd.sys.platform', 'darwin'), \
                    patch('logbook.launchd.subprocess.run', return_value=subprocess.CompletedProcess(
                        [], 0, '', 'Authority=Logbook Local Signing\n')), \
                    patch('logbook.launchd._write_app_bundle') as build:
                with self.assertRaisesRegex(ValueError, 'certificate-signed'):
                    write_launchd_package(package)
                build.assert_not_called()
            for returncode, detail in ((1, 'signature inspection failed'), (0, 'Signature=adhoc'),
                                       (1, 'code object is not signed at all')):
                with patch('logbook.launchd.sys.platform', 'darwin'), \
                        patch('logbook.launchd.subprocess.run', return_value=subprocess.CompletedProcess(
                            [], returncode, '', detail)), \
                        patch('logbook.launchd._write_app_bundle') as build:
                    if 'failed' in detail:
                        with self.assertRaisesRegex(ValueError, 'cannot establish'):
                            write_launchd_package(package)
                        build.assert_not_called()
                    else:
                        write_launchd_package(package)
                        build.assert_called_once()

    def test_signed_helper_replacement_preserves_previous_app_on_failure(self) -> None:
        for failure in ('compile', 'sign', 'verify', 'replace', 'restore', 'stale_backup', 'first_install', 'none'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                package = render_launchd_package(
                    config=load_app_config(_write_env(root)), env_path=root / '.env',
                    output_dir=root / 'generated', repo_root=root,
                )
                app = package.mount_runner
                if failure != 'first_install':
                    app.bundle_path.mkdir(parents=True)
                    (app.bundle_path / 'old').write_text('previous signed app')
                previous = app.bundle_path.with_name(app.bundle_path.name + '.previous')
                if failure == 'stale_backup':
                    previous.mkdir()
                def build(staged):
                    if failure == 'compile':
                        raise OSError('simulated compiler failure')
                    staged.bundle_path.mkdir(parents=True)
                    (staged.bundle_path / 'new').write_text('new signed app')
                def sign(command, **kwargs):
                    if failure == 'sign' and '--sign' in command or failure == 'verify' and '--verify' in command:
                        raise subprocess.CalledProcessError(1, command)
                    return subprocess.CompletedProcess(command, 0)
                original = Path.rename
                def rename(path, target):
                    if failure == 'restore' and path == previous:
                        raise OSError('simulated restore failure')
                    if failure in ('replace', 'restore', 'first_install') and path.parent.name.startswith('.mount-build-'):
                        raise OSError('simulated install failure')
                    return original(path, target)
                with patch('logbook.launchd._write_app_bundle', side_effect=build), \
                        patch('logbook.launchd.subprocess.run', side_effect=sign) as signing, \
                        patch.object(Path, 'rename', rename):
                    if failure == 'none':
                        _write_signed_app_bundle(app, 'A' * 40)
                        self.assertTrue((app.bundle_path / 'new').is_file())
                        self.assertIn('ws.prager.logbook.mount-runner', signing.call_args_list[0].args[0])
                    else:
                        with self.assertRaises((OSError, subprocess.CalledProcessError)):
                            _write_signed_app_bundle(app, 'A' * 40)
                        if failure == 'restore':
                            self.assertFalse(app.bundle_path.exists())
                            self.assertEqual((previous / 'old').read_text(), 'previous signed app')
                        elif failure == 'first_install':
                            self.assertFalse(app.bundle_path.exists())
                        else:
                            self.assertEqual((app.bundle_path / 'old').read_text(), 'previous signed app')

    def test_cli_remembers_explicit_signing_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_path = _write_env(root)
            with patch('logbook.launchd._write_signed_app_bundle') as signing, redirect_stdout(io.StringIO()):
                result = main(['launchd-render', '--env', str(env_path), '--output-dir', str(root / 'generated'),
                               '--signing-identity', 'A' * 40])
            self.assertEqual(result, 0)
            self.assertEqual(signing.call_args.args[1], 'A' * 40)

    def test_identity_is_saved_before_replacement_and_survives_signing_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = render_launchd_package(
                config=load_app_config(_write_env(root)), env_path=root / '.env',
                output_dir=root / 'generated', repo_root=root,
            )
            with patch('logbook.launchd.atomic_write_text', side_effect=OSError('disk full')), \
                    patch('logbook.launchd._write_signed_app_bundle') as signing:
                with self.assertRaises(OSError):
                    write_launchd_package(package, signing_identity='A' * 40)
                signing.assert_not_called()
            with patch('logbook.launchd._write_signed_app_bundle', side_effect=OSError('signing unavailable')):
                with self.assertRaises(OSError):
                    write_launchd_package(package, signing_identity='A' * 40)
            self.assertEqual((package.output_dir / 'signing-identity').read_text().strip(), 'A' * 40)
            with patch('logbook.launchd._write_signed_app_bundle', side_effect=OSError('signing unavailable')):
                with self.assertRaises(OSError):
                    write_launchd_package(package, signing_identity='B' * 40)
            self.assertEqual((package.output_dir / 'signing-identity').read_text().strip(), 'A' * 40)

    def test_persistent_signing_identity_is_remembered_for_future_rebuilds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = render_launchd_package(
                config=load_app_config(_write_env(root)), env_path=root / '.env',
                output_dir=root / 'generated', repo_root=root,
            )
            with patch('logbook.launchd._write_signed_app_bundle', create=True) as sign:
                write_launchd_package(package, signing_identity='A' * 40)
                self.assertEqual((package.output_dir / 'signing-identity').read_text().strip(), 'A' * 40)
                write_launchd_package(package)
                self.assertEqual(sign.call_count, 2)
                self.assertEqual(sign.call_args.args, (package.mount_runner, 'A' * 40))
                for invalid in ('', '-'):
                    (package.output_dir / 'signing-identity').write_text(invalid)
                    with self.assertRaises(ValueError):
                        write_launchd_package(package)

    def test_mount_runner_ignores_unrelated_mounts_but_checks_connected_recorder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            mount = root / "Sony"
            config = replace(config, recorder=replace(config.recorder, mount_path=mount))
            repo = root / "repo"
            repo.mkdir()
            marker = root / "pipeline-started"
            python = root / "python"
            python.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
            python.chmod(0o755)
            package = render_launchd_package(
                config=config, env_path=env_path, output_dir=root / "generated",
                repo_root=repo, python_bin=str(python),
            )
            write_launchd_package(package)
            absent = subprocess.run([str(package.mount_runner.executable_path)], capture_output=True, text=True)
            self.assertEqual(absent.returncode, 0)
            self.assertFalse(marker.exists(), absent.stdout + absent.stderr)
            self.assertIn("recorder absent", absent.stdout)
            mount.mkdir()
            connected = subprocess.run([str(package.mount_runner.executable_path)], capture_output=True, text=True)
            self.assertEqual(connected.returncode, 0)
            self.assertTrue(marker.exists())
            self.assertIn("recorder preflight failed", connected.stderr)

    def test_render_launchd_package_uses_bounded_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_path = _write_env(root)
            config = load_app_config(env_path)

            package = render_launchd_package(
                config=config,
                env_path=env_path,
                output_dir=root / "launchd",
                repo_root=root / "repo",
                python_bin="/usr/bin/python3",
            )

            api = _loads(package.api_service.content)
            self.assertEqual(api["Label"], "local.logbook.api")
            self.assertEqual(api["ProgramArguments"][0], "/usr/bin/python3")
            self.assertIn("serve-api", api["ProgramArguments"])
            self.assertTrue(api["RunAtLoad"])
            self.assertEqual(api["KeepAlive"], {"SuccessfulExit": False})
            self.assertEqual(api["ExitTimeOut"], 15)

            mount_probe = _loads(package.mount_probe.content)
            self.assertEqual(mount_probe["Label"], "local.logbook.recorder.mount-probe")
            self.assertTrue(mount_probe["StartOnMount"])
            self.assertEqual(mount_probe["ProgramArguments"][0], "/usr/bin/open")
            self.assertIn("-W", mount_probe["ProgramArguments"])
            self.assertIn("-n", mount_probe["ProgramArguments"])
            self.assertNotIn("-g", mount_probe["ProgramArguments"])
            self.assertIn(str(package.mount_runner.bundle_path), mount_probe["ProgramArguments"])
            self.assertNotIn("process-mounted-recorder", mount_probe["ProgramArguments"])
            self.assertNotIn("recorder-discover", mount_probe["ProgramArguments"])
            self.assertNotIn("copy-discovered", mount_probe["ProgramArguments"])
            self.assertNotIn("route-transcripts", mount_probe["ProgramArguments"])

            runner_info = _loads(package.mount_runner.info_plist_content)
            self.assertEqual(runner_info["CFBundleIdentifier"], "ws.prager.logbook.mount-runner")
            self.assertEqual(runner_info["CFBundleExecutable"], "LogbookMountRunner")
            self.assertIn("NSRemovableVolumesUsageDescription", runner_info)
            self.assertIn("process-mounted-recorder", package.mount_runner.source_content)
            self.assertIn("opendir", package.mount_runner.source_content)
            self.assertIn("posix_spawn", package.mount_runner.source_content)
            self.assertIn(str(root / "repo"), package.mount_runner.source_content)
            self.assertIn("REC_FILE/FOLDER01", package.mount_runner.source_content)

            retention = _loads(package.retention_audit.content)
            self.assertEqual(retention["Label"], "local.logbook.retention-audit")
            self.assertEqual(retention["StartCalendarInterval"], [{"Minute": 17}])
            self.assertIn("cleanup-audio", retention["ProgramArguments"])
            self.assertIn("--execute", retention["ProgramArguments"])
            self.assertIn("--include-recorder", retention["ProgramArguments"])

            entity_linker = _loads(package.entity_linker.content)
            self.assertEqual(entity_linker["Label"], "local.logbook.entity-linker")
            self.assertEqual(
                entity_linker["StartCalendarInterval"],
                [{"Hour": 3, "Minute": 37}],
            )
            self.assertIn("link-daily-log-entities", entity_linker["ProgramArguments"])
            self.assertIn("--execute", entity_linker["ProgramArguments"])
            self.assertIn("--months", entity_linker["ProgramArguments"])

            for plist in (api, mount_probe, retention, entity_linker):
                self.assertEqual(plist["WorkingDirectory"], str(root / "repo"))
                self.assertEqual(
                    plist["EnvironmentVariables"],
                    {"PYTHONPATH": str(root / "repo" / "src")},
                )
                self.assertIn(str(config.processing_root / "logs"), plist["StandardOutPath"])
                self.assertIn(str(config.processing_root / "logs"), plist["StandardErrorPath"])

    def test_write_launchd_package_writes_plists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            package = render_launchd_package(
                config=config,
                env_path=env_path,
                output_dir=root / "generated",
                repo_root=root / "repo",
                python_bin="/usr/bin/python3",
            )

            paths = write_launchd_package(package)

            self.assertEqual(len(paths), 4)
            self.assertTrue(package.logs_dir.exists())
            for path in paths:
                self.assertTrue(path.exists())
                self.assertEqual(path.suffix, ".plist")
                _loads(path.read_text(encoding="utf-8"))
            self.assertTrue(package.mount_runner.bundle_path.exists())
            self.assertTrue(package.mount_runner.info_plist_path.exists())
            self.assertTrue(package.mount_runner.source_path.exists())
            self.assertTrue(package.mount_runner.executable_path.exists())
            self.assertTrue(package.mount_runner.executable_path.stat().st_mode & 0o111)
            self.assertNotEqual(
                package.mount_runner.executable_path.read_bytes()[:2],
                b"#!",
            )

    def test_launchd_render_reports_guarded_retention_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_path = _write_env(root)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "launchd-render",
                        "--env",
                        str(env_path),
                        "--output-dir",
                        str(root / "launchd"),
                        "--repo-root",
                        str(root / "repo"),
                        "--python-bin",
                        "/usr/bin/python3",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn(
                "retention_cleanup_command=cleanup-audio --execute --include-recorder",
                stdout.getvalue(),
            )


def _loads(content: str) -> dict:
    return plistlib.loads(content.encode("utf-8"))


def _write_env(root: Path) -> Path:
    env_path = root / ".env"
    env_path.write_text(
        f"""
LOGBOOK_PROCESSING_ROOT={root / "VoiceIngest"}
LOGBOOK_SQLITE_PATH={root / "VoiceIngest" / "voice_ingest.sqlite"}
LOGBOOK_AUDIO_RETENTION_HOURS=168
LOGBOOK_AUDIO_CLEANUP_MODE=trash_then_delete
SONY_RECORDER_VOLUME_NAME=IC RECORDER
SONY_RECORDER_MOUNT_PATH=/Volumes/IC RECORDER
SONY_RECORDER_RECORDINGS_PATH=/REC_FILE/FOLDER01
ODIN_API_BASE_URL=http://odin.test
LOGBOOK_API_BIND_HOST=127.0.0.1
LOGBOOK_API_PORT=8787
""".lstrip(),
        encoding="utf-8",
    )
    return env_path
