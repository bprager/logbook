from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from logbook.recorder import RecordingCandidate


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RecordingJob:
    id: int
    checksum_sha256: str
    source_device: str
    source_filename: str
    source_path: str
    size_bytes: int
    modified_at: str
    parsed_recorded_at: str | None
    status: str
    first_seen_at: str
    last_seen_at: str
    copied_path: str | None = None
    copied_at: str | None = None
    odin_job_id: str | None = None
    submitted_to_odin_at: str | None = None
    transcript_path: str | None = None
    transcribed_at: str | None = None
    asr_model: str | None = None
    classification: str | None = None
    obsidian_path: str | None = None
    routed_at: str | None = None
    daily_log_path: str | None = None
    consolidated_at: str | None = None
    late_arrival_at: str | None = None


@dataclass(frozen=True)
class ActionAudit:
    id: int
    action_type: str
    target_type: str
    target_id: str
    idempotency_key: str | None
    requested_by: str
    request_payload: str
    status: str
    created_at: str

    @property
    def payload(self) -> dict:
        return json.loads(self.request_payload) if self.request_payload else {}


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row

    def close(self) -> None:
        self.connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection:
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS recording_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    checksum_sha256 TEXT NOT NULL UNIQUE,
                    source_device TEXT NOT NULL,
                    source_filename TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    modified_at TEXT NOT NULL,
                    parsed_recorded_at TEXT,
                    status TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    dry_run_seen_count INTEGER NOT NULL DEFAULT 1,
                    copied_path TEXT,
                    copied_at TEXT,
                    odin_job_id TEXT,
                    submitted_to_odin_at TEXT,
                    transcript_path TEXT,
                    transcribed_at TEXT,
                    asr_model TEXT,
                    classification TEXT,
                    obsidian_path TEXT,
                    routed_at TEXT,
                    daily_log_path TEXT,
                    consolidated_at TEXT,
                    late_arrival_at TEXT
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS action_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action_type TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    idempotency_key TEXT,
                    requested_by TEXT NOT NULL,
                    request_payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._ensure_column("action_audit", "idempotency_key", "TEXT")
            self.connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_action_audit_idempotency
                ON action_audit (
                    action_type,
                    target_type,
                    target_id,
                    idempotency_key
                )
                WHERE idempotency_key IS NOT NULL
                """
            )
            self._ensure_column("recording_jobs", "copied_path", "TEXT")
            self._ensure_column("recording_jobs", "copied_at", "TEXT")
            self._ensure_column("recording_jobs", "odin_job_id", "TEXT")
            self._ensure_column("recording_jobs", "submitted_to_odin_at", "TEXT")
            self._ensure_column("recording_jobs", "transcript_path", "TEXT")
            self._ensure_column("recording_jobs", "transcribed_at", "TEXT")
            self._ensure_column("recording_jobs", "asr_model", "TEXT")
            self._ensure_column("recording_jobs", "classification", "TEXT")
            self._ensure_column("recording_jobs", "obsidian_path", "TEXT")
            self._ensure_column("recording_jobs", "routed_at", "TEXT")
            self._ensure_column("recording_jobs", "daily_log_path", "TEXT")
            self._ensure_column("recording_jobs", "consolidated_at", "TEXT")
            self._ensure_column("recording_jobs", "late_arrival_at", "TEXT")
            self.connection.execute(
                """
                INSERT OR IGNORE INTO schema_migrations (version, applied_at)
                VALUES (?, ?)
                """,
                (SCHEMA_VERSION, utc_now_iso()),
            )

    def get_by_checksum(self, checksum_sha256: str) -> RecordingJob | None:
        row = self.connection.execute(
            """
            SELECT id, checksum_sha256, source_device, source_filename, source_path,
                   size_bytes, modified_at, parsed_recorded_at, status, first_seen_at,
                   last_seen_at, copied_path, copied_at, odin_job_id, submitted_to_odin_at,
                   transcript_path, transcribed_at, asr_model, classification,
                   obsidian_path, routed_at, daily_log_path, consolidated_at,
                   late_arrival_at
            FROM recording_jobs
            WHERE checksum_sha256 = ?
            """,
            (checksum_sha256,),
        ).fetchone()
        if row is None:
            return None
        return RecordingJob(
            id=row["id"],
            checksum_sha256=row["checksum_sha256"],
            source_device=row["source_device"],
            source_filename=row["source_filename"],
            source_path=row["source_path"],
            size_bytes=row["size_bytes"],
            modified_at=row["modified_at"],
            parsed_recorded_at=row["parsed_recorded_at"],
            status=row["status"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            copied_path=row["copied_path"],
            copied_at=row["copied_at"],
            odin_job_id=row["odin_job_id"],
            submitted_to_odin_at=row["submitted_to_odin_at"],
            transcript_path=row["transcript_path"],
            transcribed_at=row["transcribed_at"],
            asr_model=row["asr_model"],
            classification=row["classification"],
            obsidian_path=row["obsidian_path"],
            routed_at=row["routed_at"],
            daily_log_path=row["daily_log_path"],
            consolidated_at=row["consolidated_at"],
            late_arrival_at=row["late_arrival_at"],
        )

    def get_by_id(self, job_id: int) -> RecordingJob | None:
        row = self.connection.execute(
            """
            SELECT checksum_sha256
            FROM recording_jobs
            WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        return self.get_by_checksum(row["checksum_sha256"])

    def record_discovery(
        self,
        candidate: RecordingCandidate,
        checksum_sha256: str,
        source_device: str,
        seen_at: str | None = None,
    ) -> RecordingJob:
        seen_at = seen_at or utc_now_iso()
        parsed_recorded_at = (
            candidate.parsed_recorded_at.isoformat(timespec="seconds")
            if candidate.parsed_recorded_at
            else None
        )
        modified_at = candidate.modified_at.isoformat(timespec="seconds")

        with self.connection:
            self.connection.execute(
                """
                INSERT INTO recording_jobs (
                    checksum_sha256, source_device, source_filename, source_path,
                    size_bytes, modified_at, parsed_recorded_at, status,
                    first_seen_at, last_seen_at, dry_run_seen_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'discovered', ?, ?, 1)
                ON CONFLICT(checksum_sha256) DO UPDATE SET
                    source_filename = excluded.source_filename,
                    source_path = excluded.source_path,
                    size_bytes = excluded.size_bytes,
                    modified_at = excluded.modified_at,
                    parsed_recorded_at = excluded.parsed_recorded_at,
                    last_seen_at = excluded.last_seen_at,
                    dry_run_seen_count = dry_run_seen_count + 1
                """,
                (
                    checksum_sha256,
                    source_device,
                    candidate.filename,
                    str(candidate.path),
                    candidate.size_bytes,
                    modified_at,
                    parsed_recorded_at,
                    seen_at,
                    seen_at,
                ),
            )
        job = self.get_by_checksum(checksum_sha256)
        if job is None:
            raise RuntimeError("recording discovery was not written")
        return job

    def copied_jobs(self) -> list[RecordingJob]:
        rows = self.connection.execute(
            """
            SELECT checksum_sha256
            FROM recording_jobs
            WHERE status = 'copied'
            ORDER BY id
            """
        ).fetchall()
        return [
            job
            for row in rows
            if (job := self.get_by_checksum(row["checksum_sha256"])) is not None
        ]

    def log_jobs_for_consolidation(
        self,
        entry_date: str | None = None,
        include_consolidated: bool = False,
    ) -> list[RecordingJob]:
        params: tuple[str, ...]
        date_clause = ""
        if entry_date is not None:
            date_clause = "AND substr(parsed_recorded_at, 1, 10) = ?"
            params = (entry_date,)
        else:
            params = ()
        statuses = ("inbox_written", "consolidated") if include_consolidated else ("inbox_written",)
        placeholders = ", ".join("?" for _ in statuses)
        rows = self.connection.execute(
            f"""
            SELECT checksum_sha256
            FROM recording_jobs
            WHERE status IN ({placeholders})
              AND classification = 'log'
              AND parsed_recorded_at IS NOT NULL
              {date_clause}
            ORDER BY parsed_recorded_at, id
            """,
            statuses + params,
        ).fetchall()
        return [
            job
            for row in rows
            if (job := self.get_by_checksum(row["checksum_sha256"])) is not None
        ]

    def has_consolidated_log_for_date(self, entry_date: str) -> bool:
        row = self.connection.execute(
            """
            SELECT 1
            FROM recording_jobs
            WHERE status = 'consolidated'
              AND classification = 'log'
              AND substr(parsed_recorded_at, 1, 10) = ?
            LIMIT 1
            """,
            (entry_date,),
        ).fetchone()
        return row is not None

    def transcribed_jobs(self) -> list[RecordingJob]:
        rows = self.connection.execute(
            """
            SELECT checksum_sha256
            FROM recording_jobs
            WHERE status = 'transcribed'
            ORDER BY id
            """
        ).fetchall()
        return [
            job
            for row in rows
            if (job := self.get_by_checksum(row["checksum_sha256"])) is not None
        ]

    def routing_jobs(
        self,
        job_id: int | None = None,
        include_routed: bool = False,
    ) -> list[RecordingJob]:
        if job_id is not None:
            job = self.get_by_id(job_id)
            return [job] if job is not None else []

        statuses = (
            "transcribed",
            "inbox_written",
            "category_written",
            "meeting_written",
            "dead_letter_written",
        )
        status_filter = statuses if include_routed else ("transcribed",)
        placeholders = ", ".join("?" for _ in status_filter)
        rows = self.connection.execute(
            f"""
            SELECT checksum_sha256
            FROM recording_jobs
            WHERE status IN ({placeholders})
            ORDER BY id
            """,
            status_filter,
        ).fetchall()
        return [
            job
            for row in rows
            if (job := self.get_by_checksum(row["checksum_sha256"])) is not None
        ]

    def mark_copied(
        self,
        checksum_sha256: str,
        copied_path: Path,
        copied_at: str | None = None,
    ) -> RecordingJob:
        copied_at = copied_at or utc_now_iso()
        with self.connection:
            self.connection.execute(
                """
                UPDATE recording_jobs
                SET status = 'copied',
                    copied_path = ?,
                    copied_at = ?,
                    last_seen_at = ?
                WHERE checksum_sha256 = ?
                """,
                (str(copied_path), copied_at, copied_at, checksum_sha256),
            )
        job = self.get_by_checksum(checksum_sha256)
        if job is None:
            raise RuntimeError("copied recording job was not found")
        return job

    def mark_transcribed(
        self,
        checksum_sha256: str,
        odin_job_id: str,
        transcript_path: Path,
        asr_model: str,
        transcribed_at: str | None = None,
    ) -> RecordingJob:
        transcribed_at = transcribed_at or utc_now_iso()
        with self.connection:
            self.connection.execute(
                """
                UPDATE recording_jobs
                SET status = 'transcribed',
                    odin_job_id = ?,
                    submitted_to_odin_at = COALESCE(submitted_to_odin_at, ?),
                    transcript_path = ?,
                    transcribed_at = ?,
                    asr_model = ?,
                    last_seen_at = ?
                WHERE checksum_sha256 = ?
                """,
                (
                    odin_job_id,
                    transcribed_at,
                    str(transcript_path),
                    transcribed_at,
                    asr_model,
                    transcribed_at,
                    checksum_sha256,
                ),
            )
        job = self.get_by_checksum(checksum_sha256)
        if job is None:
            raise RuntimeError("transcribed recording job was not found")
        return job

    def mark_routed(
        self,
        checksum_sha256: str,
        classification: str,
        obsidian_path: Path,
        status: str,
        routed_at: str | None = None,
    ) -> RecordingJob:
        routed_at = routed_at or utc_now_iso()
        with self.connection:
            self.connection.execute(
                """
                UPDATE recording_jobs
                SET status = ?,
                    classification = ?,
                    obsidian_path = ?,
                    routed_at = ?,
                    last_seen_at = ?
                WHERE checksum_sha256 = ?
                """,
                (
                    status,
                    classification,
                    str(obsidian_path),
                    routed_at,
                    routed_at,
                    checksum_sha256,
                ),
            )
        job = self.get_by_checksum(checksum_sha256)
        if job is None:
            raise RuntimeError("routed recording job was not found")
        return job

    def mark_consolidated(
        self,
        checksum_sha256: str,
        daily_log_path: Path,
        consolidated_at: str | None = None,
    ) -> RecordingJob:
        consolidated_at = consolidated_at or utc_now_iso()
        with self.connection:
            self.connection.execute(
                """
                UPDATE recording_jobs
                SET status = 'consolidated',
                    daily_log_path = ?,
                    consolidated_at = ?,
                    last_seen_at = ?
                WHERE checksum_sha256 = ?
                """,
                (
                    str(daily_log_path),
                    consolidated_at,
                    consolidated_at,
                    checksum_sha256,
                ),
            )
        job = self.get_by_checksum(checksum_sha256)
        if job is None:
            raise RuntimeError("consolidated recording job was not found")
        return job

    def mark_late_arrival(
        self,
        checksum_sha256: str,
        late_arrival_at: str | None = None,
    ) -> RecordingJob:
        late_arrival_at = late_arrival_at or utc_now_iso()
        with self.connection:
            self.connection.execute(
                """
                UPDATE recording_jobs
                SET late_arrival_at = COALESCE(late_arrival_at, ?),
                    last_seen_at = ?
                WHERE checksum_sha256 = ?
                """,
                (late_arrival_at, late_arrival_at, checksum_sha256),
            )
        job = self.get_by_checksum(checksum_sha256)
        if job is None:
            raise RuntimeError("late-arrival recording job was not found")
        return job

    def record_action(
        self,
        action_type: str,
        target_type: str,
        target_id: str,
        request_payload: dict,
        requested_by: str = "api",
        status: str = "accepted",
        idempotency_key: str | None = None,
        created_at: str | None = None,
    ) -> ActionAudit:
        created_at = created_at or utc_now_iso()
        payload = json.dumps(request_payload, sort_keys=True, separators=(",", ":"))
        if idempotency_key:
            existing = self.get_action_by_idempotency(
                action_type=action_type,
                target_type=target_type,
                target_id=target_id,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return existing
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO action_audit (
                    action_type, target_type, target_id, idempotency_key,
                    requested_by, request_payload, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action_type,
                    target_type,
                    target_id,
                    idempotency_key,
                    requested_by,
                    payload,
                    status,
                    created_at,
                ),
            )
        audit = self.get_action(cursor.lastrowid)
        if audit is None:
            raise RuntimeError("action audit record was not written")
        return audit

    def get_action(self, action_id: int) -> ActionAudit | None:
        row = self.connection.execute(
            """
            SELECT id, action_type, target_type, target_id, idempotency_key, requested_by,
                   request_payload, status, created_at
            FROM action_audit
            WHERE id = ?
            """,
            (action_id,),
        ).fetchone()
        if row is None:
            return None
        return ActionAudit(
            id=row["id"],
            action_type=row["action_type"],
            target_type=row["target_type"],
            target_id=row["target_id"],
            idempotency_key=row["idempotency_key"],
            requested_by=row["requested_by"],
            request_payload=row["request_payload"],
            status=row["status"],
            created_at=row["created_at"],
        )

    def get_action_by_idempotency(
        self,
        action_type: str,
        target_type: str,
        target_id: str,
        idempotency_key: str,
    ) -> ActionAudit | None:
        row = self.connection.execute(
            """
            SELECT id
            FROM action_audit
            WHERE action_type = ?
              AND target_type = ?
              AND target_id = ?
              AND idempotency_key = ?
            """,
            (action_type, target_type, target_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        return self.get_action(row["id"])

    def _ensure_column(self, table_name: str, column_name: str, column_type: str) -> None:
        columns = {
            row["name"]
            for row in self.connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name not in columns:
            self.connection.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
            )


def open_ledger(path: Path, initialize: bool = False) -> Ledger:
    if initialize:
        path.parent.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(path)
    if initialize:
        ledger.initialize()
    return ledger


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
