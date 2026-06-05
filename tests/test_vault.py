from __future__ import annotations

import os
import subprocess
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

    def test_session_recovers_clean_diverged_vault_with_merge_after_ff_only_sync_failure(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault_root = root / "vault"
            vault_root.mkdir()
            _init_git_repo(vault_root)
            (vault_root / "base.md").write_text("base\n", encoding="utf-8")
            _git(vault_root, "add", "--", "base.md")
            _git(vault_root, "commit", "-m", "base")
            _git(vault_root, "checkout", "-b", "remote-main")
            (vault_root / "remote.md").write_text("remote\n", encoding="utf-8")
            _git(vault_root, "add", "--", "remote.md")
            _git(vault_root, "commit", "-m", "remote")
            _git(vault_root, "update-ref", "refs/remotes/origin/main", "HEAD")
            _git(vault_root, "checkout", "main")
            (vault_root / "local.md").write_text("local\n", encoding="utf-8")
            _git(vault_root, "add", "--", "local.md")
            _git(vault_root, "commit", "-m", "local")
            log_path = root / "calls.log"
            cli_path = _fake_cli(root, log_path)
            workflow = ObsidianVaultWorkflow(
                config=ObsidianConfig(
                    cli_bin=str(cli_path),
                    vault_repo_url="https://github.com/bprager/obs-vault.git",
                    vault_local_path=vault_root,
                    sync_command="{cli_bin} ff-fail {vault_path}",
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

            commands = workflow.report().commands
            self.assertEqual(commands[0].name, "sync")
            self.assertEqual(commands[0].returncode, 128)
            self.assertEqual(commands[1].name, "sync_ff_only_merge_recovery")
            self.assertEqual(commands[1].returncode, 0)
            self.assertTrue((vault_root / "remote.md").exists())
            self.assertFalse((root / "vault-workflow.lock").exists())

    def test_session_reports_failed_merge_recovery_after_ff_only_sync_failure(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault_root = root / "vault"
            vault_root.mkdir()
            _init_git_repo(vault_root)
            conflict = vault_root / "conflict.md"
            conflict.write_text("base\n", encoding="utf-8")
            _git(vault_root, "add", "--", "conflict.md")
            _git(vault_root, "commit", "-m", "base")
            _git(vault_root, "checkout", "-b", "remote-main")
            conflict.write_text("remote\n", encoding="utf-8")
            _git(vault_root, "add", "--", "conflict.md")
            _git(vault_root, "commit", "-m", "remote")
            _git(vault_root, "update-ref", "refs/remotes/origin/main", "HEAD")
            _git(vault_root, "checkout", "main")
            conflict.write_text("local\n", encoding="utf-8")
            _git(vault_root, "add", "--", "conflict.md")
            _git(vault_root, "commit", "-m", "local")
            cli_path = _fake_cli(root, root / "calls.log")
            workflow = ObsidianVaultWorkflow(
                config=ObsidianConfig(
                    cli_bin=str(cli_path),
                    vault_repo_url="https://github.com/bprager/obs-vault.git",
                    vault_local_path=vault_root,
                    sync_command="{cli_bin} ff-fail {vault_path}",
                ),
                vault_root=vault_root,
                lock_root=root,
            )

            with self.assertRaisesRegex(
                VaultWorkflowError,
                "sync fast-forward recovery exited",
            ):
                with workflow.session("route-notes"):
                    pass

            self.assertEqual(workflow.report().commands[1].name, "sync_ff_only_merge_recovery")
            self.assertNotEqual(workflow.report().commands[1].returncode, 0)
            self.assertFalse((root / "vault-workflow.lock").exists())

    def test_session_stashes_and_ignores_obsidian_workspace_before_sync(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault_root = root / "vault"
            workspace = vault_root / ".obsidian" / "workspace.json"
            workspace.parent.mkdir(parents=True)
            workspace.write_text("{}\n", encoding="utf-8")
            subprocess.run(["git", "init"], cwd=vault_root, check=True, capture_output=True)
            subprocess.run(
                ["git", "add", "--", ".obsidian/workspace.json"],
                cwd=vault_root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Logbook Test",
                    "-c",
                    "user.email=logbook@example.invalid",
                    "commit",
                    "-m",
                    "track workspace",
                ],
                cwd=vault_root,
                check=True,
                capture_output=True,
            )
            workspace.write_text('{"dirty":true}\n', encoding="utf-8")
            cli_path = _fake_cli(root, root / "calls.log")
            workflow = ObsidianVaultWorkflow(
                config=ObsidianConfig(
                    cli_bin=str(cli_path),
                    vault_repo_url="https://github.com/bprager/obs-vault.git",
                    vault_local_path=vault_root,
                    sync_command="{cli_bin} sync {vault_path}",
                ),
                vault_root=vault_root,
                lock_root=root,
            )

            with workflow.session("route-notes"):
                pass

            listed = subprocess.run(
                ["git", "ls-files", "-v", "--", ".obsidian/workspace.json"],
                cwd=vault_root,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertTrue(listed.stdout.startswith("S "))
            stashes = subprocess.run(
                ["git", "stash", "list"],
                cwd=vault_root,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("Logbook preserve Obsidian workspace state", stashes.stdout)

    def test_session_creates_generated_roots_before_stage(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault_root = root / "vault"
            vault_root.mkdir()
            subprocess.run(["git", "init"], cwd=vault_root, check=True, capture_output=True)
            workflow = ObsidianVaultWorkflow(
                config=ObsidianConfig(
                    cli_bin="/bin/echo",
                    vault_repo_url="https://github.com/bprager/obs-vault.git",
                    vault_local_path=vault_root,
                    stage_command='git -C {vault_path} add -- "20 - Notes"',
                ),
                vault_root=vault_root,
                lock_root=root,
            )

            with workflow.session("route-notes"):
                pass

            self.assertTrue((vault_root / "20 - Notes").is_dir())

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
        "if [ \"$1\" = \"ff-fail\" ]; then\n"
        "  printf '%s\\n' \"fatal: Not possible to fast-forward, aborting.\" >&2\n"
        "  exit 128\n"
        "fi\n"
        f"printf '%s\\n' \"$*\" >> {log_path}\n",
        encoding="utf-8",
    )
    os.chmod(cli_path, 0o755)
    return cli_path


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    _git(path, "config", "user.name", "Logbook Test")
    _git(path, "config", "user.email", "logbook@example.invalid")


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)
