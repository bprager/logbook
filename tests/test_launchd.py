import plistlib
import tempfile
from pathlib import Path
from unittest import TestCase

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
            self.assertIn("recorder-discover", mount_probe["ProgramArguments"])
            self.assertNotIn("copy-discovered", mount_probe["ProgramArguments"])
            self.assertNotIn("route-transcripts", mount_probe["ProgramArguments"])

            retention = _loads(package.retention_audit.content)
            self.assertEqual(retention["Label"], "local.logbook.retention-audit")
            self.assertEqual(retention["StartCalendarInterval"], [{"Minute": 17}])
            self.assertIn("retention-status", retention["ProgramArguments"])

            for plist in (api, mount_probe, retention):
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

            self.assertEqual(len(paths), 3)
            self.assertTrue(package.logs_dir.exists())
            for path in paths:
                self.assertTrue(path.exists())
                self.assertEqual(path.suffix, ".plist")
                _loads(path.read_text(encoding="utf-8"))


def _loads(content: str) -> dict:
    return plistlib.loads(content.encode("utf-8"))


def _write_env(root: Path) -> Path:
    env_path = root / ".env"
    env_path.write_text(
        f"""
LOGBOOK_PROCESSING_ROOT={root / "VoiceIngest"}
LOGBOOK_SQLITE_PATH={root / "VoiceIngest" / "voice_ingest.sqlite"}
LOGBOOK_AUDIO_RETENTION_HOURS=24
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
