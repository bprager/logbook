from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from logbook.config import AppConfig
from logbook.consolidation import group_log_entries, source_inbox_dir
from logbook.ledger import open_ledger
from logbook.markdown import render_open_log_preview
from logbook.paths import daily_log_path, open_log_preview_path
from logbook.writers import FilesystemNoteWriter, NoteWriteError, NoteWriter


@dataclass(frozen=True)
class OpenLogPreviewResult:
    vault_root: Path
    entry_date: str
    preview_path: Path
    status: str
    entry_count: int


def write_open_log_preview(
    config: AppConfig,
    vault_root: Path,
    note_writer: NoteWriter | None = None,
    entry_date: str | None = None,
) -> OpenLogPreviewResult:
    note_writer = note_writer or FilesystemNoteWriter()
    target_date = entry_date or datetime.now().date().isoformat()
    preview_path = open_log_preview_path(vault_root)
    ledger = open_ledger(config.sqlite_path, initialize=True)
    try:
        jobs = ledger.log_jobs_for_consolidation(
            entry_date=target_date,
            include_consolidated=True,
        )
        grouped = group_log_entries(jobs)
    finally:
        ledger.close()

    entries = grouped.get(target_date, [])
    canonical_path = _canonical_path_for(vault_root, target_date)
    content = render_open_log_preview(
        entry_date=target_date,
        entries=entries,
        generated_from=source_inbox_dir(datetime.fromisoformat(f"{target_date}T00:00:00")),
        canonical_daily_log=canonical_path.relative_to(vault_root).as_posix(),
    )
    try:
        note_writer.write_note(preview_path, content)
    except NoteWriteError:
        return OpenLogPreviewResult(
            vault_root=vault_root,
            entry_date=target_date,
            preview_path=preview_path,
            status="failed_note_write",
            entry_count=len(entries),
        )

    return OpenLogPreviewResult(
        vault_root=vault_root,
        entry_date=target_date,
        preview_path=preview_path,
        status="preview_written",
        entry_count=len(entries),
    )


def _canonical_path_for(vault_root: Path, entry_date: str) -> Path:
    return daily_log_path(vault_root, datetime.fromisoformat(f"{entry_date}T00:00:00"))
