from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from logbook.config import ObsidianConfig
from logbook.vault import ObsidianVaultWorkflow, VaultWorkflowError


class VaultWorkflowTests(TestCase):
    def test_preflight_reports_missing_cli(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = ObsidianVaultWorkflow(
                config=ObsidianConfig(
                    cli_bin=str(root / "missing-obsidian"),
                    vault_repo_url="https://github.com/bprager/obs-vault.git",
                    vault_local_path=root / "vault",
                ),
                vault_root=root / "vault",
                lock_root=root,
            )

            preflight = workflow.preflight()

            self.assertFalse(preflight.operational)
            self.assertFalse(next(check for check in preflight.checks if check.name == "obsidian_cli").ok)

    def test_session_runs_configured_commands_in_order_and_releases_lock(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault_root = root / "vault"
            vault_root.mkdir()
            log_path = root / "calls.log"
            cli_path = _fake_cli(root, log_path)
            workflow = ObsidianVaultWorkflow(
                config=ObsidianConfig(
                    cli_bin=str(cli_path),
                    vault_repo_url="https://github.com/bprager/obs-vault.git",
                    vault_local_path=vault_root,
                    sync_command="{cli_bin} sync {vault_path}",
                    stage_command="{cli_bin} stage {vault_path}",
                    status_command="{cli_bin} status {vault_path}",
                    commit_command="{cli_bin} commit {vault_path} {message}",
                    push_command="{cli_bin} push {vault_path}",
                ),
                vault_root=vault_root,
                lock_root=root,
            )

            with workflow.session("route-notes"):
                (vault_root / "note.md").write_text("hello\n", encoding="utf-8")

            self.assertEqual(
                log_path.read_text(encoding="utf-8").splitlines(),
                [
                    f"sync {vault_root}",
                    f"stage {vault_root}",
                    f"status {vault_root}",
                    f"commit {vault_root} route-notes",
                    f"push {vault_root}",
                ],
            )
            self.assertFalse((root / "vault-workflow.lock").exists())

    def test_session_raises_on_failed_command(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault_root = root / "vault"
            vault_root.mkdir()
            cli_path = _fake_cli(root, root / "calls.log")
            workflow = ObsidianVaultWorkflow(
                config=ObsidianConfig(
                    cli_bin=str(cli_path),
                    vault_repo_url="https://github.com/bprager/obs-vault.git",
                    vault_local_path=vault_root,
                    sync_command="{cli_bin} fail {vault_path}",
                ),
                vault_root=vault_root,
                lock_root=root,
            )

            with self.assertRaises(VaultWorkflowError):
                with workflow.session("route-notes"):
                    pass
            self.assertFalse((root / "vault-workflow.lock").exists())

    def test_preflight_checks_registered_vault_when_name_is_configured(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault_root = root / "vault"
            vault_root.mkdir()
            log_path = root / "calls.log"
            cli_path = _fake_cli(root, log_path)
            workflow = ObsidianVaultWorkflow(
                config=ObsidianConfig(
                    cli_bin=str(cli_path),
                    vault_repo_url="https://github.com/bprager/obs-vault.git",
                    vault_local_path=vault_root,
                    vault_name="obs-vault",
                ),
                vault_root=vault_root,
                lock_root=root,
            )

            preflight = workflow.preflight()

            self.assertTrue(preflight.operational)
            self.assertTrue(
                next(
                    check
                    for check in preflight.checks
                    if check.name == "obsidian_vault_registered"
                ).ok
            )

    def test_preflight_fails_when_named_vault_is_not_registered(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault_root = root / "vault"
            vault_root.mkdir()
            cli_path = root / "fake-obsidian"
            cli_path.write_text("#!/bin/sh\nexit 13\n", encoding="utf-8")
            os.chmod(cli_path, 0o755)
            workflow = ObsidianVaultWorkflow(
                config=ObsidianConfig(
                    cli_bin=str(cli_path),
                    vault_repo_url="https://github.com/bprager/obs-vault.git",
                    vault_local_path=vault_root,
                    vault_name="obs-vault",
                ),
                vault_root=vault_root,
                lock_root=root,
            )

            preflight = workflow.preflight()

            self.assertFalse(preflight.operational)
            self.assertFalse(
                next(
                    check
                    for check in preflight.checks
                    if check.name == "obsidian_vault_registered"
                ).ok
            )


def _fake_cli(root: Path, log_path: Path) -> Path:
    cli_path = root / "fake-obsidian"
    cli_path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"fail\" ]; then exit 42; fi\n"
        f"printf '%s\\n' \"$*\" >> {log_path}\n",
        encoding="utf-8",
    )
    os.chmod(cli_path, 0o755)
    return cli_path
