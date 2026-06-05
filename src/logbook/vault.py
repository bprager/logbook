from __future__ import annotations

import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from logbook.config import ObsidianConfig


class VaultWorkflowError(RuntimeError):
    """Raised when the Obsidian vault workflow cannot proceed safely."""


@dataclass(frozen=True)
class VaultCheck:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class VaultCommandRun:
    name: str
    skipped: bool
    returncode: int | None
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.skipped or self.returncode == 0


@dataclass(frozen=True)
class VaultPreflight:
    checks: tuple[VaultCheck, ...]

    @property
    def operational(self) -> bool:
        return all(check.ok for check in self.checks)


@dataclass(frozen=True)
class VaultWorkflowReport:
    preflight: VaultPreflight
    commands: tuple[VaultCommandRun, ...]


class ObsidianVaultWorkflow:
    def __init__(self, config: ObsidianConfig, vault_root: Path, lock_root: Path) -> None:
        self.config = config
        self.vault_root = vault_root
        self.lock_root = lock_root
        self._lock_path = lock_root / "vault-workflow.lock"
        self._commands: list[VaultCommandRun] = []

    def preflight(self) -> VaultPreflight:
        cli_path = _resolve_executable(self.config.cli_bin)
        checks = [
            VaultCheck(
                "obsidian_cli",
                cli_path is not None,
                cli_path or f"not found: {self.config.cli_bin}",
            ),
            VaultCheck(
                "vault_parent",
                self.vault_root.parent.exists() and self.vault_root.parent.is_dir(),
                str(self.vault_root.parent),
            ),
            VaultCheck(
                "repo_url",
                bool(self.config.vault_repo_url),
                _redact_repo_url(self.config.vault_repo_url),
            ),
            VaultCheck(
                "target_vault",
                self.vault_root == self.config.vault_local_path or self.vault_root.exists(),
                "configured vault path or existing test vault",
            ),
        ]
        if cli_path is not None and self.config.vault_name:
            checks.append(_registered_vault_check(self.config.cli_bin, self.config.vault_name))
        return VaultPreflight(checks=tuple(checks))

    def session(self, message: str) -> "VaultWriteSession":
        return VaultWriteSession(self, message)

    def report(self) -> VaultWorkflowReport:
        return VaultWorkflowReport(self.preflight(), tuple(self._commands))

    def _acquire_lock(self) -> None:
        self.lock_root.mkdir(parents=True, exist_ok=True)
        try:
            self._lock_path.mkdir()
        except FileExistsError as error:
            raise VaultWorkflowError(f"vault workflow lock already exists: {self._lock_path}") from error

    def _release_lock(self) -> None:
        if self._lock_path.exists():
            self._lock_path.rmdir()

    def _run_step(self, name: str, template: str | None, message: str) -> VaultCommandRun:
        if not template:
            run = VaultCommandRun(name=name, skipped=True, returncode=None)
            self._commands.append(run)
            return run

        command = _render_command(
            template=template,
            cli_bin=self.config.cli_bin,
            vault_root=self.vault_root,
            repo_url=self.config.vault_repo_url,
            message=message,
        )
        completed = subprocess.run(
            command,
            cwd=self.vault_root if self.vault_root.exists() else self.vault_root.parent,
            text=True,
            capture_output=True,
            check=False,
        )
        run = VaultCommandRun(
            name=name,
            skipped=False,
            returncode=completed.returncode,
            stdout=completed.stdout.strip(),
            stderr=completed.stderr.strip(),
        )
        self._commands.append(run)
        if not run.ok:
            if name == "sync" and _is_fast_forward_sync_failure(run):
                recovery = self._recover_fast_forward_sync_failure()
                if recovery.ok:
                    return recovery
            raise VaultWorkflowError(
                f"vault workflow step failed: {name} exited {completed.returncode}"
            )
        return run

    def _recover_fast_forward_sync_failure(self) -> VaultCommandRun:
        completed = subprocess.run(
            ["git", "-C", str(self.vault_root), "merge", "origin/main"],
            text=True,
            capture_output=True,
            check=False,
        )
        run = VaultCommandRun(
            name="sync_ff_only_merge_recovery",
            skipped=False,
            returncode=completed.returncode,
            stdout=completed.stdout.strip(),
            stderr=completed.stderr.strip(),
        )
        self._commands.append(run)
        if not run.ok:
            raise VaultWorkflowError(
                "vault workflow step failed: "
                f"sync fast-forward recovery exited {completed.returncode}"
            )
        return run

    def _preserve_obsidian_workspace(self) -> None:
        workspace_path = self.vault_root / ".obsidian" / "workspace.json"
        if not workspace_path.exists() or not (self.vault_root / ".git").exists():
            return

        self._run_git_workspace_step(
            "track_obsidian_workspace_for_stash",
            ["update-index", "--no-skip-worktree", "--", ".obsidian/workspace.json"],
            allow_failure=True,
        )
        dirty = subprocess.run(
            [
                "git",
                "-C",
                str(self.vault_root),
                "diff",
                "--quiet",
                "--",
                ".obsidian/workspace.json",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if dirty.returncode == 1:
            self._run_git_workspace_step(
                "stash_obsidian_workspace",
                [
                    "stash",
                    "push",
                    "-m",
                    "Logbook preserve Obsidian workspace state",
                    "--",
                    ".obsidian/workspace.json",
                ],
            )
        elif dirty.returncode != 0:
            raise VaultWorkflowError(
                "vault workflow step failed: "
                f"check_obsidian_workspace exited {dirty.returncode}"
            )

        self._run_git_workspace_step(
            "ignore_obsidian_workspace",
            ["update-index", "--skip-worktree", "--", ".obsidian/workspace.json"],
        )

    def _run_git_workspace_step(
        self,
        name: str,
        args: list[str],
        *,
        allow_failure: bool = False,
    ) -> VaultCommandRun:
        completed = subprocess.run(
            ["git", "-C", str(self.vault_root), *args],
            text=True,
            capture_output=True,
            check=False,
        )
        run = VaultCommandRun(
            name=name,
            skipped=False,
            returncode=completed.returncode,
            stdout=completed.stdout.strip(),
            stderr=completed.stderr.strip(),
        )
        self._commands.append(run)
        if not run.ok and not allow_failure:
            raise VaultWorkflowError(
                f"vault workflow step failed: {name} exited {completed.returncode}"
            )
        return run

    def _ensure_generated_roots(self) -> None:
        for relative in (
            "06 - Timestamps",
            "10 - Logs",
            "20 - Notes",
            "30 - Meetings",
            "40 - Reviews",
            "99 - Dead Letters",
        ):
            (self.vault_root / relative).mkdir(parents=True, exist_ok=True)


class VaultWriteSession:
    def __init__(self, workflow: ObsidianVaultWorkflow, message: str) -> None:
        self.workflow = workflow
        self.message = message

    def __enter__(self) -> "VaultWriteSession":
        preflight = self.workflow.preflight()
        if not preflight.operational:
            details = ", ".join(
                f"{check.name}={check.detail}" for check in preflight.checks if not check.ok
            )
            raise VaultWorkflowError(f"vault preflight failed: {details}")
        self.workflow._acquire_lock()
        try:
            self.workflow._preserve_obsidian_workspace()
            self.workflow._run_step("sync", self.workflow.config.sync_command, self.message)
        except Exception:
            self.workflow._release_lock()
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        try:
            if exc_type is None:
                self.workflow._ensure_generated_roots()
                self.workflow._run_step(
                    "stage",
                    self.workflow.config.stage_command,
                    self.message,
                )
                self.workflow._run_step(
                    "status",
                    self.workflow.config.status_command,
                    self.message,
                )
                self.workflow._run_step(
                    "commit",
                    self.workflow.config.commit_command,
                    self.message,
                )
                self.workflow._run_step(
                    "push",
                    self.workflow.config.push_command,
                    self.message,
                )
        finally:
            self.workflow._release_lock()
        return False


def _resolve_executable(cli_bin: str) -> str | None:
    path = Path(cli_bin)
    if path.is_absolute():
        return str(path) if path.exists() and path.is_file() else None
    return shutil.which(cli_bin)


def _render_command(
    template: str,
    cli_bin: str,
    vault_root: Path,
    repo_url: str,
    message: str,
) -> list[str]:
    rendered = template.format(
        cli_bin=cli_bin,
        vault_path=str(vault_root),
        repo_url=repo_url,
        message=message,
    )
    return shlex.split(rendered)


def _redact_repo_url(repo_url: str) -> str:
    if "@" not in repo_url:
        return repo_url
    scheme, rest = repo_url.split("://", 1) if "://" in repo_url else ("", repo_url)
    host_and_path = rest.split("@", 1)[1]
    return f"{scheme}://<redacted>@{host_and_path}" if scheme else f"<redacted>@{host_and_path}"


def _is_fast_forward_sync_failure(run: VaultCommandRun) -> bool:
    detail = f"{run.stdout}\n{run.stderr}"
    return (
        run.returncode == 128
        and "Not possible to fast-forward" in detail
    )


def _registered_vault_check(cli_bin: str, vault_name: str) -> VaultCheck:
    completed = subprocess.run(
        [cli_bin, "list", "--vault", vault_name],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode == 0:
        return VaultCheck("obsidian_vault_registered", True, vault_name)
    detail = (completed.stderr or completed.stdout).strip().splitlines()
    message = detail[0] if detail else f"vault not available: {vault_name}"
    return VaultCheck("obsidian_vault_registered", False, message)
