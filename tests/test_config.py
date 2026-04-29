from pathlib import Path
from unittest import TestCase

from logbook.config import ConfigError, load_app_config, load_recorder_config, parse_env_file


class ConfigTests(TestCase):
    def test_parse_env_file_strips_quotes_and_ignores_comments(self) -> None:
        env_path = Path(self._testMethodName) / ".env"
        env_path.parent.mkdir(exist_ok=True)
        self.addCleanup(_cleanup_tree, env_path.parent)
        env_path.write_text(
            """
# comment
SONY_RECORDER_VOLUME_NAME="IC RECORDER"
SONY_RECORDER_MOUNT_PATH='/Volumes/IC RECORDER'
SONY_RECORDER_RECORDINGS_PATH=/REC_FILE/FOLDER01
EMPTY=
""".lstrip(),
            encoding="utf-8",
        )

        values = parse_env_file(env_path)

        self.assertEqual(values["SONY_RECORDER_VOLUME_NAME"], "IC RECORDER")
        self.assertEqual(values["SONY_RECORDER_MOUNT_PATH"], "/Volumes/IC RECORDER")
        self.assertEqual(values["SONY_RECORDER_RECORDINGS_PATH"], "/REC_FILE/FOLDER01")
        self.assertEqual(values["EMPTY"], "")

    def test_load_recorder_config_requires_core_values(self) -> None:
        env_path = Path(self._testMethodName) / ".env"
        env_path.parent.mkdir(exist_ok=True)
        self.addCleanup(_cleanup_tree, env_path.parent)
        env_path.write_text("SONY_RECORDER_VOLUME_NAME=IC RECORDER\n", encoding="utf-8")

        with self.assertRaisesRegex(ConfigError, "SONY_RECORDER_MOUNT_PATH"):
            load_recorder_config(env_path)

    def test_load_app_config_includes_obsidian_workflow_settings(self) -> None:
        env_path = Path(self._testMethodName) / ".env"
        env_path.parent.mkdir(exist_ok=True)
        self.addCleanup(_cleanup_tree, env_path.parent)
        env_path.write_text(
            """
LOGBOOK_PROCESSING_ROOT=/tmp/logbook
SONY_RECORDER_VOLUME_NAME=IC RECORDER
SONY_RECORDER_MOUNT_PATH=/Volumes/IC RECORDER
SONY_RECORDER_RECORDINGS_PATH=/REC_FILE/FOLDER01
ODIN_API_BASE_URL=http://odin.test
OBSIDIAN_CLI_BIN=/usr/local/bin/obsidian
OBSIDIAN_VAULT_REPO_URL=https://github.com/bprager/obs-vault.git
OBSIDIAN_VAULT_LOCAL_PATH=/Users/bernd/Obsidian/obs-vault
OBSIDIAN_SYNC_COMMAND={cli_bin} sync {vault_path}
OBSIDIAN_STAGE_COMMAND=git -C {vault_path} add 10 - Logs
LOGBOOK_API_BIND_HOST=127.0.0.1
LOGBOOK_API_PORT=8787
LOGBOOK_READ_TOKEN=read-secret
""".lstrip(),
            encoding="utf-8",
        )

        config = load_app_config(env_path)

        self.assertIsNotNone(config.obsidian)
        self.assertEqual(config.obsidian.cli_bin, "/usr/local/bin/obsidian")
        self.assertEqual(
            str(config.obsidian.vault_local_path),
            "/Users/bernd/Obsidian/obs-vault",
        )
        self.assertEqual(config.obsidian.sync_command, "{cli_bin} sync {vault_path}")
        self.assertEqual(config.obsidian.stage_command, "git -C {vault_path} add 10 - Logs")
        self.assertIsNotNone(config.api)
        self.assertEqual(config.api.bind_host, "127.0.0.1")
        self.assertEqual(config.api.port, 8787)
        self.assertEqual(config.api.read_token, "read-secret")


def _cleanup_tree(path: Path) -> None:
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_file():
            child.unlink()
        elif child.is_dir():
            child.rmdir()
    if path.exists():
        path.rmdir()
