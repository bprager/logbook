from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from logbook.config import AppConfig
from logbook.ledger import RecordingJob, open_ledger, utc_now_iso
from logbook.vault import ObsidianVaultWorkflow, VaultPreflight


FINAL_SYNC_STATUSES = {
    "consolidated",
    "category_written",
    "dead_letter_discarded",
    "dead_letter_written",
    "meeting_written",
}


@dataclass(frozen=True)
class VaultSyncMarkItem:
    job: RecordingJob
    status: str
    paths: tuple[str, ...]
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class VaultSyncMarkResult:
    vault_root: Path
    dry_run: bool
    vault_head: str | None
    origin_head: str | None
    preflight: VaultPreflight
    items: tuple[VaultSyncMarkItem, ...]

    @property
    def markable_count(self) -> int:
        return sum(1 for item in self.items if item.status == "markable")

    @property
    def marked_count(self) -> int:
        return sum(1 for item in self.items if item.status == "marked")

    @property
    def blocked_count(self) -> int:
        return sum(1 for item in self.items if item.status == "blocked")

    @property
    def already_synced_count(self) -> int:
        return sum(1 for item in self.items if item.status == "already_synced")


def mark_vault_synced_jobs(
    config: AppConfig,
    *,
    dry_run: bool = True,
    synced_at: str | None = None,
) -> VaultSyncMarkResult:
    if config.obsidian is None:
        raise ValueError("missing Obsidian configuration")

    vault_root = config.obsidian.vault_local_path
    workflow = ObsidianVaultWorkflow(
        config=config.obsidian,
        vault_root=vault_root,
        lock_root=config.processing_root,
    )
    preflight = workflow.preflight()
    vault_head, origin_head, git_blockers = _vault_git_state(vault_root)
    global_blockers = list(git_blockers)
    if not preflight.operational:
        global_blockers.append("vault_preflight_failed")

    ledger = open_ledger(config.sqlite_path, initialize=True)
    try:
        jobs = ledger.vault_sync_candidate_jobs()
        items: list[VaultSyncMarkItem] = []
        mark_time = synced_at or utc_now_iso()
        for job in jobs:
            paths = _required_vault_paths(job)
            blockers = [*global_blockers, *_job_blockers(vault_root, job, paths)]
            if blockers:
                status = "blocked"
            elif job.vault_synced_at:
                status = "already_synced"
            elif dry_run:
                status = "markable"
            else:
                ledger.mark_vault_synced(job.checksum_sha256, mark_time)
                status = "marked"
            items.append(
                VaultSyncMarkItem(
                    job=job,
                    status=status,
                    paths=paths,
                    blockers=tuple(blockers),
                )
            )
    finally:
        ledger.close()

    return VaultSyncMarkResult(
        vault_root=vault_root,
        dry_run=dry_run,
        vault_head=vault_head,
        origin_head=origin_head,
        preflight=preflight,
        items=tuple(items),
    )


def _required_vault_paths(job: RecordingJob) -> tuple[str, ...]:
    paths: list[str] = []
    if job.obsidian_path:
        paths.append(job.obsidian_path)
    if job.status == "consolidated" and job.daily_log_path:
        paths.append(job.daily_log_path)
    return tuple(dict.fromkeys(paths))


def _job_blockers(vault_root: Path, job: RecordingJob, paths: tuple[str, ...]) -> tuple[str, ...]:
    blockers: list[str] = []
    if job.status not in FINAL_SYNC_STATUSES:
        blockers.append("status_not_final")
    if not paths:
        blockers.append("missing_vault_paths")
    for path in paths:
        if _path_is_unsafe(path):
            blockers.append(f"unsafe_vault_path:{path}")
        elif not _path_exists_in_head(vault_root, path):
            blockers.append(f"missing_in_vault_head:{path}")
    return tuple(blockers)


def _vault_git_state(vault_root: Path) -> tuple[str | None, str | None, tuple[str, ...]]:
    if not (vault_root / ".git").exists():
        return None, None, ("vault_not_git_repo",)
    head = _git_output(vault_root, "rev-parse", "HEAD")
    origin = _git_output(vault_root, "rev-parse", "origin/main")
    blockers: list[str] = []
    if head is None:
        blockers.append("missing_vault_head")
    if origin is None:
        blockers.append("missing_origin_main")
    if head is not None and origin is not None and head != origin:
        blockers.append("vault_head_not_pushed")
    return head, origin, tuple(blockers)


def _path_exists_in_head(vault_root: Path, relative_path: str) -> bool:
    completed = subprocess.run(
        ["git", "cat-file", "-e", f"HEAD:{relative_path}"],
        cwd=vault_root,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0


def _git_output(vault_root: Path, *args: str) -> str | None:
    completed = subprocess.run(
        ["git", *args],
        cwd=vault_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _path_is_unsafe(relative_path: str) -> bool:
    path = Path(relative_path)
    return path.is_absolute() or ".." in path.parts or not relative_path.strip()
