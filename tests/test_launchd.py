import plistlib
import io
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import TestCase

from logbook.cli import main
from logbook.config import load_app_config
from logbook.launchd import render_launchd_package, write_launchd_package


class LaunchdPackagingTests(TestCase):
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
