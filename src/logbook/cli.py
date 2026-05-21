from __future__ import annotations

import argparse
import json
import os
import select
import shutil
import sqlite3
import subprocess
import sys
import termios
import time
import tty
from pathlib import Path
from urllib import request

from logbook.backup import run_backup, run_restore_drill
from logbook.config import AppConfig, ConfigError, load_app_config, load_recorder_config
from logbook.consolidation import consolidate_daily_logs
from logbook.copying import copy_discovered_recordings, copy_discovered_recordings_with_retries
from logbook.dead_letters import (
    assign_dead_letter_to_log,
    discard_dead_letter,
    list_dead_letters,
    rescue_dead_letter_as_meeting,
)
from logbook.diarization import diarize_meetings, diarize_meetings_with_fake_odin
from logbook.entity_linker import link_daily_log_entities
from logbook.insights import extract_insights
from logbook.ingest import run_ingest_dry_run
from logbook.launchd import render_launchd_package, write_launchd_package
from logbook.ledger import open_ledger
from logbook.memory_graph import (
    Neo4jMemgraphClient,
    apply_memory_graph_plan,
    apply_memory_graph_repair_plan,
    build_memory_graph_plan,
    build_memory_graph_repair_plan,
    check_memory_graph_health,
    query_memory_graph_plan,
)
from logbook.odin import HttpOdinClient
from logbook.observer import (
    build_observer_snapshot,
    filter_observer_snapshot,
    observer_snapshot_from_dict,
    render_full_observer_dashboard,
    render_observer_snapshot,
)
from logbook.preview import write_open_log_preview
from logbook.recorder import RecorderAccessError, discover_recordings, validate_recorder
from logbook.retention import execute_audio_cleanup, plan_audio_cleanup
from logbook.routing import route_transcripts
from logbook.telemetry import SQLitePipelineReporter
from logbook.transcription import transcribe_copied, transcribe_copied_with_fake_odin
from logbook.vault import ObsidianVaultWorkflow, VaultWorkflowError
from logbook.vault_sync import mark_vault_synced_jobs
from logbook.writers import FilesystemNoteWriter, ObsidianCliNoteWriter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="logbook")
    subparsers = parser.add_subparsers(dest="command", required=True)

    recorder_parser = subparsers.add_parser(
        "recorder-discover",
        help="validate recorder config and list MP3 files without modifying anything",
    )
    recorder_parser.add_argument("--env", type=Path, default=Path(".env"))

    ingest_parser = subparsers.add_parser(
        "ingest-dry-run",
        help="checksum recorder MP3 files and compare them to the SQLite ledger",
    )
    ingest_parser.add_argument("--env", type=Path, default=Path(".env"))
    ingest_parser.add_argument(
        "--record-discovery",
        action="store_true",
        help="write discovered checksums to the local SQLite ledger without copying audio",
    )

    copy_parser = subparsers.add_parser(
        "copy-discovered",
        help="copy recorder MP3 files into the local inbox and verify checksums",
    )
    copy_parser.add_argument("--env", type=Path, default=Path(".env"))

    process_mount_parser = subparsers.add_parser(
        "process-mounted-recorder",
        help="copy, transcribe, diarize, route, and sync mounted recorder files",
    )
    process_mount_parser.add_argument("--env", type=Path, default=Path(".env"))

    fake_transcribe_parser = subparsers.add_parser(
        "fake-transcribe-copied",
        help="exercise the odin client boundary with fake transcripts for copied files",
    )
    fake_transcribe_parser.add_argument("--env", type=Path, default=Path(".env"))

    transcribe_parser = subparsers.add_parser(
        "transcribe-copied",
        help="send copied recordings to the configured odin transcription endpoint",
    )
    transcribe_parser.add_argument("--env", type=Path, default=Path(".env"))

    odin_health_parser = subparsers.add_parser(
        "odin-health",
        help="check the configured odin worker health endpoint without submitting audio",
    )
    odin_health_parser.add_argument("--env", type=Path, default=Path(".env"))

    serve_odin_parser = subparsers.add_parser(
        "serve-odin-worker",
        help="start the internal FastAPI odin ASR worker on the configured host",
    )
    serve_odin_parser.add_argument("--env", type=Path, default=Path(".env"))
    serve_odin_parser.add_argument("--host", default="127.0.0.1")
    serve_odin_parser.add_argument("--port", type=int, default=8765)
    serve_odin_parser.add_argument(
        "--worker-root",
        type=Path,
        default=None,
        help="directory for worker audio staging; defaults to LOGBOOK_PROCESSING_ROOT/odin-worker",
    )

    diarize_parser = subparsers.add_parser(
        "diarize-meetings",
        help="send transcribed meeting jobs to the configured odin diarization endpoint",
    )
    diarize_parser.add_argument("--env", type=Path, default=Path(".env"))

    fake_diarize_parser = subparsers.add_parser(
        "fake-diarize-meetings",
        help="exercise the odin diarization boundary for transcribed meeting jobs",
    )
    fake_diarize_parser.add_argument("--env", type=Path, default=Path(".env"))

    route_parser = subparsers.add_parser(
        "route-transcripts",
        help="route transcribed jobs into an explicit test vault path",
    )
    route_parser.add_argument("--env", type=Path, default=Path(".env"))
    route_parser.add_argument(
        "--vault",
        type=Path,
        required=True,
        help="vault root to write; use a test vault until Obsidian sync is approved",
    )
    route_parser.add_argument(
        "--vault-workflow",
        choices=("none", "preflight", "obsidian"),
        default="none",
        help="wrap routing in the Obsidian CLI vault workflow",
    )
    route_parser.add_argument(
        "--writer",
        choices=("filesystem", "obsidian-cli"),
        default="filesystem",
        help="write routed notes directly or through obsidian-cli create",
    )
    route_parser.add_argument(
        "--commit-message",
        default="Update Logbook generated notes",
        help="message passed to configured vault commit command template",
    )
    route_parser.add_argument(
        "--job-id",
        type=int,
        default=None,
        help="route exactly one ledger job, even if it was routed before",
    )
    route_parser.add_argument(
        "--include-routed",
        action="store_true",
        help="include jobs that were already routed, for controlled vault backfills",
    )

    consolidate_parser = subparsers.add_parser(
        "consolidate-logs",
        help="render canonical daily logs from routed log inbox entries",
    )
    consolidate_parser.add_argument("--env", type=Path, default=Path(".env"))
    consolidate_parser.add_argument(
        "--vault",
        type=Path,
        required=True,
        help="vault root to write canonical daily logs",
    )
    consolidate_parser.add_argument(
        "--writer",
        choices=("filesystem", "obsidian-cli"),
        default="filesystem",
        help="write daily logs directly or through obsidian-cli create",
    )
    consolidate_parser.add_argument(
        "--date",
        default=None,
        help="optional YYYY-MM-DD date to consolidate",
    )

    preview_parser = subparsers.add_parser(
        "open-log-preview",
        help="render the generated non-canonical open log preview note",
    )
    preview_parser.add_argument("--env", type=Path, default=Path(".env"))
    preview_parser.add_argument(
        "--vault",
        type=Path,
        required=True,
        help="vault root to write the open log preview",
    )
    preview_parser.add_argument(
        "--writer",
        choices=("filesystem", "obsidian-cli"),
        default="filesystem",
        help="write the preview directly or through obsidian-cli create",
    )
    preview_parser.add_argument(
        "--date",
        default=None,
        help="optional YYYY-MM-DD date to preview; defaults to today",
    )

    entity_link_parser = subparsers.add_parser(
        "link-daily-log-entities",
        help="link known people, event, and object entity mentions in canonical daily logs",
    )
    entity_link_parser.add_argument("--env", type=Path, default=Path(".env"))
    entity_link_parser.add_argument(
        "--vault",
        type=Path,
        default=None,
        help="vault root to update; defaults to OBSIDIAN_VAULT_LOCAL_PATH",
    )
    entity_link_parser.add_argument(
        "--months",
        type=int,
        default=3,
        help="calendar months of daily logs to scan, counting back from today",
    )
    entity_link_parser.add_argument(
        "--execute",
        action="store_true",
        help="write links into daily logs; without this flag the command is a dry run",
    )

    dead_letter_parser = subparsers.add_parser(
        "manage-dead-letters",
        help="list, assign, rescue, or discard dead letters with an audit trail",
    )
    dead_letter_parser.add_argument("--env", type=Path, default=Path(".env"))
    dead_letter_parser.add_argument(
        "--action",
        choices=("list", "assign", "rescue", "discard"),
        default="list",
        help="operation to perform; rescue supports target=meeting",
    )
    dead_letter_parser.add_argument(
        "--job-id",
        type=int,
        default=None,
        help="dead-letter job id for assign, rescue, or discard",
    )
    dead_letter_parser.add_argument(
        "--target",
        choices=("log", "meeting"),
        default="log",
        help="target route for assign or rescue",
    )
    dead_letter_parser.add_argument(
        "--vault",
        type=Path,
        default=None,
        help="vault root to update; defaults to OBSIDIAN_VAULT_LOCAL_PATH",
    )
    dead_letter_parser.add_argument(
        "--linker-months",
        type=int,
        default=3,
        help="calendar months for the post-consolidation entity linker",
    )
    dead_letter_parser.add_argument(
        "--requested-by",
        default="operator",
        help="actor recorded in action_audit",
    )
    dead_letter_parser.add_argument(
        "--reason",
        default=None,
        help="optional reason recorded in action_audit",
    )
    dead_letter_parser.add_argument(
        "--execute",
        action="store_true",
        help="mutate ledger/vault; without this flag assign/rescue/discard are dry runs",
    )

    vault_parser = subparsers.add_parser(
        "vault-preflight",
        help="validate configured Obsidian CLI and vault paths without writing",
    )
    vault_parser.add_argument("--env", type=Path, default=Path(".env"))
    vault_parser.add_argument(
        "--vault",
        type=Path,
        default=None,
        help="optional vault root override; defaults to OBSIDIAN_VAULT_LOCAL_PATH",
    )

    mark_vault_synced_parser = subparsers.add_parser(
        "mark-vault-synced",
        help="prove generated vault files are pushed, then optionally mark ledger jobs synced",
    )
    mark_vault_synced_parser.add_argument("--env", type=Path, default=Path(".env"))
    mark_vault_synced_parser.add_argument(
        "--execute",
        action="store_true",
        help="write vault_synced_at for markable jobs; without this flag the command is a dry run",
    )

    insights_parser = subparsers.add_parser(
        "extract-insights",
        help="write non-canonical summary and action review notes",
    )
    insights_parser.add_argument("--env", type=Path, default=Path(".env"))
    insights_parser.add_argument(
        "--vault",
        type=Path,
        required=True,
        help="vault root to write insight review notes",
    )
    insights_parser.add_argument(
        "--writer",
        choices=("filesystem", "obsidian-cli"),
        default="filesystem",
        help="write review notes directly or through obsidian-cli create",
    )
    insights_parser.add_argument(
        "--job-id",
        type=int,
        default=None,
        help="extract insights for exactly one ledger job",
    )

    memory_sync_parser = subparsers.add_parser(
        "memory-graph-sync",
        help="plan or execute proof-carrying memory graph sync into Memgraph",
    )
    memory_sync_parser.add_argument("--env", type=Path, default=Path(".env"))
    memory_sync_parser.add_argument(
        "--job-id",
        type=int,
        default=None,
        help="sync exactly one ledger job; defaults to all jobs",
    )
    memory_sync_parser.add_argument(
        "--execute",
        action="store_true",
        help="upsert planned nodes and relationships into configured Memgraph",
    )

    memory_query_parser = subparsers.add_parser(
        "memory-graph-query",
        help="answer bounded memory questions from the local proof-carrying graph plan",
    )
    memory_query_parser.add_argument("--env", type=Path, default=Path(".env"))
    memory_query_parser.add_argument(
        "--query",
        choices=(
            "open-loops",
            "unresolved-actions",
            "recent-decisions",
            "topic-trails",
            "weekly-diff",
        ),
        required=True,
    )
    memory_query_parser.add_argument(
        "--job-id",
        type=int,
        default=None,
        help="query exactly one ledger job; defaults to all jobs",
    )

    memory_health_parser = subparsers.add_parser(
        "memory-graph-health",
        help="compare the local memory graph plan with live Memgraph counts without writing",
    )
    memory_health_parser.add_argument("--env", type=Path, default=Path(".env"))

    memory_repair_parser = subparsers.add_parser(
        "memory-graph-repair",
        help="dry-run or repair exact Logbook memory graph drift without resetting Memgraph",
    )
    memory_repair_parser.add_argument("--env", type=Path, default=Path(".env"))
    memory_repair_parser.add_argument(
        "--prune-stale",
        action="store_true",
        help="delete stale Logbook-owned graph IDs that are absent from the local plan",
    )
    memory_repair_parser.add_argument(
        "--execute",
        action="store_true",
        help="upsert missing planned IDs and optionally prune stale IDs",
    )

    memory_resolve_parser = subparsers.add_parser(
        "memory-action-resolve",
        help="dry-run or mark a memory action candidate resolved in SQLite",
    )
    memory_resolve_parser.add_argument("--env", type=Path, default=Path(".env"))
    memory_resolve_parser.add_argument(
        "--action-id",
        required=True,
        help="stable ActionCandidate ID from memory-graph-query open-loops",
    )
    memory_resolve_parser.add_argument(
        "--resolved-by",
        default="operator",
        help="actor recorded in the durable action review row",
    )
    memory_resolve_parser.add_argument(
        "--note",
        default=None,
        help="optional resolution note stored with the review row",
    )
    memory_resolve_parser.add_argument(
        "--execute",
        action="store_true",
        help="write the resolution; without this flag the command is a dry run",
    )

    serve_api_parser = subparsers.add_parser(
        "serve-api",
        help="start the read-only FastAPI status API",
    )
    serve_api_parser.add_argument("--env", type=Path, default=Path(".env"))
    serve_api_parser.add_argument("--host", default=None)
    serve_api_parser.add_argument("--port", type=int, default=None)

    watch_web_parser = subparsers.add_parser(
        "watch-web",
        help="start the modern read-only web observer UI",
    )
    watch_web_parser.add_argument("--env", type=Path, default=Path(".env"))
    watch_web_parser.add_argument("--host", default="127.0.0.1")
    watch_web_parser.add_argument("--port", type=int, default=8790)

    watch_parser = subparsers.add_parser(
        "watch",
        help="print a compact read-only observer snapshot of recent pipeline state",
    )
    watch_parser.add_argument("--env", type=Path, default=Path(".env"))
    watch_parser.add_argument(
        "--api",
        default=None,
        help="read observer snapshots from a Logbook API base URL instead of local SQLite",
    )
    watch_parser.add_argument(
        "--read-token-env",
        default=None,
        help="environment variable containing the API bearer token",
    )
    watch_parser.add_argument(
        "--once",
        action="store_true",
        help="render one snapshot and exit",
    )
    watch_parser.add_argument(
        "--json",
        action="store_true",
        help="emit the observer snapshot as JSON",
    )
    watch_parser.add_argument(
        "--ui",
        choices=("compact", "full", "curses"),
        default="compact",
        help="terminal layout for text output",
    )
    watch_parser.add_argument(
        "--refresh-interval",
        type=float,
        default=2.0,
        help="seconds between live refreshes",
    )
    watch_parser.add_argument(
        "--max-refreshes",
        type=int,
        default=None,
        help="stop after this many refreshes; useful for scripts and tests",
    )
    watch_parser.add_argument(
        "--theme",
        choices=("auto", "day", "night"),
        default="auto",
        help="terminal appearance; auto uses local day/night hour",
    )
    watch_parser.add_argument(
        "--no-color",
        action="store_true",
        help="disable ANSI colors",
    )
    watch_parser.add_argument(
        "--status",
        choices=("all", "failed", "success", "dead_letter"),
        default="all",
        help="filter recent sections",
    )
    watch_parser.add_argument(
        "--fail-on",
        choices=("never", "failure", "stale", "any"),
        default="never",
        help="exit nonzero when the snapshot contains the selected condition",
    )

    retention_status_parser = subparsers.add_parser(
        "retention-status",
        help="report retention cleanup configuration without deleting audio",
    )
    retention_status_parser.add_argument("--env", type=Path, default=Path(".env"))

    cleanup_plan_parser = subparsers.add_parser(
        "cleanup-plan",
        help="plan audio cleanup eligibility without deleting audio",
    )
    cleanup_plan_parser.add_argument("--env", type=Path, default=Path(".env"))

    cleanup_audio_parser = subparsers.add_parser(
        "cleanup-audio",
        help="execute eligible local audio cleanup; recorder cleanup requires --include-recorder",
    )
    cleanup_audio_parser.add_argument("--env", type=Path, default=Path(".env"))
    cleanup_audio_parser.add_argument(
        "--execute",
        action="store_true",
        help="perform eligible cleanup actions; without this flag the command is a dry run",
    )
    cleanup_audio_parser.add_argument(
        "--include-recorder",
        action="store_true",
        help="also delete eligible checksum-verified recorder source files",
    )

    launchd_render_parser = subparsers.add_parser(
        "launchd-render",
        help="render launchd plists for the Logbook API, mount probe, and retention audit",
    )
    launchd_render_parser.add_argument("--env", type=Path, default=Path(".env"))
    launchd_render_parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="directory for generated plists; defaults to LOGBOOK_PROCESSING_ROOT/launchd",
    )
    launchd_render_parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="repository root used as launchd WorkingDirectory",
    )
    launchd_render_parser.add_argument(
        "--python-bin",
        default=sys.executable,
        help="Python executable launchd should run",
    )

    backup_run_parser = subparsers.add_parser(
        "backup-run",
        help="plan or write a restorable non-audio backup artifact set",
    )
    backup_run_parser.add_argument("--env", type=Path, default=Path(".env"))
    backup_run_parser.add_argument(
        "--backup-root",
        default=None,
        help="local path or host:/path target; defaults to LOGBOOK_BACKUP_ROOT",
    )
    backup_run_parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="repository root for configuration templates",
    )
    backup_run_parser.add_argument(
        "--execute",
        action="store_true",
        help="write backup artifacts; without this flag the command is a dry run",
    )

    restore_drill_parser = subparsers.add_parser(
        "backup-restore-drill",
        help="open a backup ledger read-only and validate counts without mutating production",
    )
    restore_drill_parser.add_argument("--env", type=Path, default=Path(".env"))
    restore_drill_parser.add_argument(
        "--backup",
        required=True,
        help="backup directory or host:/path containing manifest.json",
    )

    args = parser.parse_args(argv)

    if args.command == "recorder-discover":
        return _recorder_discover(args.env)
    if args.command == "ingest-dry-run":
        return _ingest_dry_run(args.env, record_discovery=args.record_discovery)
    if args.command == "copy-discovered":
        return _copy_discovered(args.env)
    if args.command == "process-mounted-recorder":
        return _process_mounted_recorder(args.env)
    if args.command == "fake-transcribe-copied":
        return _fake_transcribe_copied(args.env)
    if args.command == "transcribe-copied":
        return _transcribe_copied(args.env)
    if args.command == "odin-health":
        return _odin_health(args.env)
    if args.command == "serve-odin-worker":
        return _serve_odin_worker(args.env, args.host, args.port, args.worker_root)
    if args.command == "diarize-meetings":
        return _diarize_meetings(args.env)
    if args.command == "fake-diarize-meetings":
        return _fake_diarize_meetings(args.env)
    if args.command == "route-transcripts":
        return _route_transcripts(
            args.env,
            args.vault,
            vault_workflow_mode=args.vault_workflow,
            writer_mode=args.writer,
            job_id=args.job_id,
            include_routed=args.include_routed,
            commit_message=args.commit_message,
        )
    if args.command == "consolidate-logs":
        return _consolidate_logs(args.env, args.vault, args.writer, args.date)
    if args.command == "open-log-preview":
        return _open_log_preview(args.env, args.vault, args.writer, args.date)
    if args.command == "link-daily-log-entities":
        return _link_daily_log_entities(args.env, args.vault, args.months, args.execute)
    if args.command == "manage-dead-letters":
        return _manage_dead_letters(
            args.env,
            action=args.action,
            job_id=args.job_id,
            target=args.target,
            vault_root=args.vault,
            linker_months=args.linker_months,
            requested_by=args.requested_by,
            reason=args.reason,
            execute=args.execute,
        )
    if args.command == "vault-preflight":
        return _vault_preflight(args.env, args.vault)
    if args.command == "mark-vault-synced":
        return _mark_vault_synced(args.env, execute=args.execute)
    if args.command == "extract-insights":
        return _extract_insights(args.env, args.vault, args.writer, args.job_id)
    if args.command == "memory-graph-sync":
        return _memory_graph_sync(args.env, args.job_id, execute=args.execute)
    if args.command == "memory-graph-query":
        return _memory_graph_query(args.env, args.query, args.job_id)
    if args.command == "memory-graph-health":
        return _memory_graph_health(args.env)
    if args.command == "memory-graph-repair":
        return _memory_graph_repair(args.env, args.prune_stale, execute=args.execute)
    if args.command == "memory-action-resolve":
        return _memory_action_resolve(
            args.env,
            action_id=args.action_id,
            resolved_by=args.resolved_by,
            note=args.note,
            execute=args.execute,
        )
    if args.command == "serve-api":
        return _serve_api(args.env, args.host, args.port)
    if args.command == "watch-web":
        return _watch_web(args.env, args.host, args.port)
    if args.command == "watch":
        return _watch(
            args.env,
            api_url=args.api,
            read_token_env=args.read_token_env,
            once=args.once,
            json_output=args.json,
            refresh_interval=args.refresh_interval,
            max_refreshes=args.max_refreshes,
            theme=args.theme,
            no_color=args.no_color,
            ui=args.ui,
            status_filter=args.status,
            fail_on=args.fail_on,
        )
    if args.command == "retention-status":
        return _retention_status(args.env)
    if args.command == "cleanup-plan":
        return _cleanup_plan(args.env)
    if args.command == "cleanup-audio":
        return _cleanup_audio(args.env, execute=args.execute, include_recorder=args.include_recorder)
    if args.command == "launchd-render":
        return _launchd_render(args.env, args.output_dir, args.repo_root, args.python_bin)
    if args.command == "backup-run":
        return _backup_run(args.env, args.backup_root, args.repo_root, execute=args.execute)
    if args.command == "backup-restore-drill":
        return _backup_restore_drill(args.env, args.backup)

    parser.error(f"unsupported command: {args.command}")
    return 2


def _recorder_discover(env_path: Path) -> int:
    try:
        config = load_recorder_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    validation = validate_recorder(config)
    print("Recorder discovery dry run")
    print(f"env_path={env_path}")
    print(f"expected_volume={validation.expected_volume_name}")
    print(f"configured_mount_path={validation.configured_mount_path}")
    print(f"resolved_mount_path={validation.resolved_mount_path}")
    print(f"recordings_dir={validation.recordings_dir}")
    print(f"exists={_yes_no(validation.exists)}")
    print(f"readable={_yes_no(validation.readable)}")
    print(f"writable={_yes_no(validation.writable)}")

    for warning in validation.warnings:
        print(f"warning={warning}")

    if not validation.operational:
        print("operational=no")
        return 1

    try:
        recordings = discover_recordings(validation.recordings_dir)
    except RecorderAccessError as error:
        print(f"warning={error}")
        print("operational=no")
        return 1
    print("operational=yes")
    print(f"mp3_count={len(recordings)}")
    print("would_ingest:")
    for recording in recordings:
        parsed = (
            recording.parsed_recorded_at.strftime("%Y-%m-%d %H:%M")
            if recording.parsed_recorded_at
            else "unparsed"
        )
        modified = recording.modified_at.strftime("%Y-%m-%d %H:%M:%S")
        match = _match_label(recording.timestamp_matches_mtime)
        print(
            f"- filename={recording.filename} size={recording.size_bytes} "
            f"parsed_recorded_at={parsed} modified_at={modified} timestamp_match={match}"
        )

    return 0


def _ingest_dry_run(env_path: Path, record_discovery: bool) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    result = run_ingest_dry_run(config, record_discovery=record_discovery)
    validation = result.validation
    print("Ingest dry run")
    print(f"env_path={env_path}")
    print(f"record_discovery={_yes_no(record_discovery)}")
    print("copy_audio=no")
    print("delete_audio=no")
    print("transcribe_audio=no")
    print("write_obsidian=no")
    print(f"ledger_path={result.ledger_path}")
    print(f"ledger_written={_yes_no(result.ledger_written)}")
    print(f"recordings_dir={validation.recordings_dir}")
    print(f"operational={_yes_no(validation.operational)}")
    for warning in validation.warnings:
        print(f"warning={warning}")
    if result.discovery_error:
        print(f"warning={result.discovery_error}")
    if not validation.operational or result.discovery_error:
        return 1

    print(f"mp3_count={len(result.items)}")
    print(f"new_count={result.new_count}")
    print(f"known_count={result.known_count}")
    print("would_ingest:")
    for item in result.items:
        recording = item.candidate
        parsed = (
            recording.parsed_recorded_at.strftime("%Y-%m-%d %H:%M")
            if recording.parsed_recorded_at
            else "unparsed"
        )
        short_checksum = item.checksum_sha256[:12]
        job = item.ledger_job_id if item.ledger_job_id is not None else "-"
        print(
            f"- filename={recording.filename} size={recording.size_bytes} "
            f"sha256={short_checksum} ledger_status={item.ledger_status} "
            f"job_id={job} parsed_recorded_at={parsed}"
        )

    return 0


def _copy_discovered(env_path: Path) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    result = copy_discovered_recordings(config)
    validation = result.validation
    print("Copy discovered recordings")
    print(f"env_path={env_path}")
    print("delete_audio=no")
    print("transcribe_audio=no")
    print("write_obsidian=no")
    print(f"ledger_path={result.ledger_path}")
    print(f"inbox_dir={result.inbox_dir}")
    print(f"recordings_dir={validation.recordings_dir}")
    print(f"operational={_yes_no(validation.operational)}")
    for warning in validation.warnings:
        print(f"warning={warning}")
    if not validation.operational:
        return 1

    print(f"mp3_count={len(result.items)}")
    print(f"copied_count={result.copied_count}")
    print(f"skipped_count={result.skipped_count}")
    print(f"failed_count={result.failed_count}")
    print("copy_results:")
    for item in result.items:
        copied_path = item.copied_path if item.copied_path is not None else "-"
        print(
            f"- filename={item.candidate.filename} status={item.status} "
            f"job_id={item.ledger_job_id} copied_path={copied_path}"
        )

    return 1 if result.failed_count else 0


def _process_mounted_recorder(env_path: Path) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    if config.obsidian is None:
        print("config_error: missing Obsidian configuration", file=sys.stderr)
        return 2

    print("Process mounted recorder")
    print(f"env_path={env_path}")
    print("delete_audio=no")
    print("delete_recorder_audio=no")
    reporter = SQLitePipelineReporter.start(
        config.sqlite_path,
        command="process-mounted-recorder",
        heartbeat_interval_seconds=15.0,
    )
    exit_code = 1

    try:
        reporter.start_stage("copy")
        copy_result = copy_discovered_recordings_with_retries(
            config,
            progress_callback=lambda current, total: (
                reporter.advance_stage("copy", progress_current=current, progress_total=total)
                if total
                else None
            ),
        )
        print(f"copy_attempt_count={copy_result.attempt_count}")
        print(f"copy_copied_count={copy_result.copied_count}")
        print(f"copy_skipped_count={copy_result.skipped_count}")
        print(f"copy_failed_count={copy_result.failed_count}")
        if copy_result.discovery_error:
            print(f"warning={copy_result.discovery_error}")
        for warning in copy_result.validation.warnings:
            print(f"warning={warning}")
        copy_stage_ok = copy_result.validation.operational and copy_result.failed_count == 0
        reporter.finish_stage(
            "copy",
            event="succeeded" if copy_stage_ok else "failed",
            safe_detail=copy_result.discovery_error,
        )

        try:
            reporter.start_stage("transcribe")
            odin_client = HttpOdinClient(config.odin)
            transcription_result = transcribe_copied(config=config, client=odin_client)
        except Exception as error:
            reporter.finish_stage("transcribe", event="failed", safe_detail=str(error))
            print(f"odin_error={error}", file=sys.stderr)
            return 1
        print(f"transcribed_count={transcription_result.transcribed_count}")
        print(f"transcription_skipped_count={transcription_result.skipped_count}")
        print(f"transcription_failed_count={transcription_result.failed_count}")
        if transcription_result.failed_count:
            reporter.finish_stage("transcribe", event="failed")
            return 1
        reporter.finish_stage("transcribe", event="succeeded")

        try:
            reporter.start_stage("diarize")
            diarization_result = diarize_meetings(config=config, client=HttpOdinClient(config.odin))
        except Exception as error:
            reporter.finish_stage("diarize", event="failed", safe_detail=str(error))
            print(f"diarization_error={error}", file=sys.stderr)
            return 1
        print(f"diarized_count={diarization_result.diarized_count}")
        print(f"diarization_skipped_count={diarization_result.skipped_count}")
        print(f"diarization_failed_count={diarization_result.failed_count}")
        if diarization_result.failed_count:
            reporter.finish_stage("diarize", event="failed")
            return 1
        reporter.finish_stage("diarize", event="succeeded")

        reporter.start_stage("route")
        routing_job_ids = _routing_job_ids(config)
        print(f"routing_candidate_count={len(routing_job_ids)}")
        if not routing_job_ids:
            if _vault_has_generated_changes(config):
                print("route_transcripts=skipped_pending_vault_changes")
                if not _commit_pending_vault_changes(config):
                    reporter.finish_stage("route", event="failed")
                    return 1
            else:
                print("route_transcripts=skipped_no_candidates")
            reporter.finish_stage("route", event="succeeded")
            reporter.start_stage("consolidate")
            if not _consolidate_pending_logs_with_vault_workflow(config):
                reporter.finish_stage("consolidate", event="failed")
                return 1
            reporter.advance_stage("consolidate", progress_current=1, progress_total=1)
            reporter.finish_stage("consolidate", event="succeeded")
            reporter.start_stage("vault_sync")
            sync_ok = _mark_vault_synced_and_sync_memory(config, env_path)
            if sync_ok:
                reporter.advance_stage("vault_sync", progress_current=1, progress_total=1)
            reporter.finish_stage("vault_sync", event="succeeded" if sync_ok else "failed")
            exit_code = 0 if copy_stage_ok and sync_ok else 1
            return exit_code

        if not _route_with_vault_workflow(config):
            reporter.finish_stage("route", event="failed")
            return 1
        reporter.advance_stage(
            "route",
            progress_current=len(routing_job_ids),
            progress_total=len(routing_job_ids),
        )
        reporter.finish_stage("route", event="succeeded")

        reporter.start_stage("consolidate")
        if not _consolidate_pending_logs_with_vault_workflow(config):
            reporter.finish_stage("consolidate", event="failed")
            return 1
        reporter.advance_stage("consolidate", progress_current=1, progress_total=1)
        reporter.finish_stage("consolidate", event="succeeded")

        reporter.start_stage("vault_sync")
        sync_ok = _mark_vault_synced_and_sync_memory(config, env_path)
        if sync_ok:
            reporter.advance_stage("vault_sync", progress_current=1, progress_total=1)
        reporter.finish_stage("vault_sync", event="succeeded" if sync_ok else "failed")
        exit_code = 0 if copy_stage_ok and sync_ok else 1
        return exit_code
    finally:
        reporter.finish_run(
            status="succeeded" if exit_code == 0 else "failed",
            exit_code=exit_code,
        )


def _mark_vault_synced_and_sync_memory(config: AppConfig, env_path: Path) -> bool:
    if _vault_has_generated_changes(config):
        print("vault_pending_changes_detected=yes")
        if not _commit_pending_vault_changes(config):
            return False

    try:
        vault_sync_result = mark_vault_synced_jobs(config, dry_run=False)
    except ValueError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return False
    print(f"vault_sync_marked_count={vault_sync_result.marked_count}")
    print(f"vault_sync_already_synced_count={vault_sync_result.already_synced_count}")
    print(f"vault_sync_blocked_count={vault_sync_result.blocked_count}")
    if vault_sync_result.blocked_count:
        return False

    marked_job_ids = tuple(item.job.id for item in vault_sync_result.items if item.status == "marked")
    if marked_job_ids:
        _sync_memory_graph_for_jobs(config, marked_job_ids, env_path=env_path)
    return True


def _route_with_vault_workflow(config: AppConfig) -> bool:
    workflow = ObsidianVaultWorkflow(
        config=config.obsidian,
        vault_root=config.obsidian.vault_local_path,
        lock_root=config.processing_root,
    )
    preflight = workflow.preflight()
    if not preflight.operational:
        _print_vault_preflight(preflight)
        return False
    note_writer = ObsidianCliNoteWriter(
        config=config.obsidian,
        vault_root=config.obsidian.vault_local_path,
    )
    try:
        routing_result = route_transcripts(
            config,
            vault_root=config.obsidian.vault_local_path,
            vault_workflow=workflow,
            note_writer=note_writer,
            commit_message="Update Logbook generated notes from mounted recorder",
        )
    except VaultWorkflowError as error:
        print(f"vault_workflow_error: {error}", file=sys.stderr)
        return False
    print(f"routed_count={routing_result.routed_count}")
    print(f"routing_failed_count={routing_result.failed_count}")
    print(f"meeting_count={sum(1 for item in routing_result.items if item.status == 'meeting_written')}")
    if routing_result.failed_count:
        return False
    return True


def _consolidate_pending_logs_with_vault_workflow(config: AppConfig) -> bool:
    pending_count = _pending_log_consolidation_count(config)
    print(f"consolidation_candidate_count={pending_count}")
    if pending_count == 0:
        print("consolidate_logs=skipped_no_candidates")
        return True

    if config.obsidian is None:
        print("config_error: missing Obsidian configuration", file=sys.stderr)
        return False

    workflow = ObsidianVaultWorkflow(
        config=config.obsidian,
        vault_root=config.obsidian.vault_local_path,
        lock_root=config.processing_root,
    )
    preflight = workflow.preflight()
    if not preflight.operational:
        _print_vault_preflight(preflight)
        return False

    note_writer = ObsidianCliNoteWriter(
        config=config.obsidian,
        vault_root=config.obsidian.vault_local_path,
    )
    try:
        with workflow.session("Update Logbook daily logs from mounted recorder"):
            result = consolidate_daily_logs(
                config,
                vault_root=config.obsidian.vault_local_path,
                note_writer=note_writer,
            )
    except VaultWorkflowError as error:
        print(f"vault_workflow_error: {error}", file=sys.stderr)
        return False

    print(f"consolidated_count={result.consolidated_count}")
    print(f"consolidation_failed_count={result.failed_count}")
    for item in result.items:
        print(
            "consolidation_result="
            f"{item.status} date={item.entry_date} entries={item.entry_count}"
        )
    return result.failed_count == 0


def _pending_log_consolidation_count(config: AppConfig) -> int:
    ledger = open_ledger(config.sqlite_path, initialize=True)
    try:
        return len(ledger.log_jobs_for_consolidation())
    finally:
        ledger.close()


def _commit_pending_vault_changes(config: AppConfig) -> bool:
    workflow = ObsidianVaultWorkflow(
        config=config.obsidian,
        vault_root=config.obsidian.vault_local_path,
        lock_root=config.processing_root,
    )
    try:
        with workflow.session("Update Logbook generated notes from mounted recorder"):
            pass
    except VaultWorkflowError as error:
        print(f"vault_workflow_error: {error}", file=sys.stderr)
        return False
    print("vault_pending_changes_committed=yes")
    return True


def _vault_has_generated_changes(config: AppConfig) -> bool:
    if config.obsidian is None:
        return False
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(config.obsidian.vault_local_path),
            "status",
            "--porcelain",
            "--",
            "06 - Timestamps",
            "10 - Logs",
            "20 - Notes",
            "30 - Meetings",
            "40 - Reviews",
            "99 - Dead Letters",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    return bool(completed.stdout.strip()) if completed.returncode == 0 else False


def _routing_job_ids(config: AppConfig) -> tuple[int, ...]:
    ledger = open_ledger(config.sqlite_path, initialize=True)
    try:
        return tuple(job.id for job in ledger.routing_jobs())
    finally:
        ledger.close()


def _sync_memory_graph_for_jobs(
    config: AppConfig,
    job_ids: tuple[int, ...],
    *,
    env_path: Path,
    timeout_seconds: float = 90,
) -> None:
    if config.memgraph is None:
        print("memory_graph_sync=skipped_missing_memgraph_config")
        return

    written_nodes = 0
    written_relationships = 0
    timed_out = 0
    failed = 0
    for job_id in job_ids:
        command = [
            sys.executable,
            "-m",
            "logbook.cli",
            "memory-graph-sync",
            "--env",
            str(env_path),
            "--job-id",
            str(job_id),
            "--execute",
        ]
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            timed_out += 1
            print(f"memory_graph_sync=timed_out job_id={job_id}")
            continue
        if completed.returncode != 0:
            failed += 1
            warning = (completed.stderr or completed.stdout).strip().splitlines()
            detail = warning[-1] if warning else f"exit_code={completed.returncode}"
            print(f"memory_graph_sync=failed job_id={job_id} warning={detail}")
            continue
        nodes, relationships = _parse_memory_graph_sync_output(completed.stdout)
        written_nodes += nodes
        written_relationships += relationships

    status = "ok" if timed_out == 0 and failed == 0 else "partial"
    print(f"memory_graph_sync={status}")
    print(f"memory_graph_jobs_synced={len(job_ids)}")
    print(f"memory_graph_nodes_written={written_nodes}")
    print(f"memory_graph_relationships_written={written_relationships}")
    print(f"memory_graph_jobs_timed_out={timed_out}")
    print(f"memory_graph_jobs_failed={failed}")


def _parse_memory_graph_sync_output(output: str) -> tuple[int, int]:
    nodes = 0
    relationships = 0
    for line in output.splitlines():
        if line.startswith("nodes_written="):
            nodes = int(line.split("=", 1)[1])
        elif line.startswith("relationships_written="):
            relationships = int(line.split("=", 1)[1])
    return nodes, relationships


def _fake_transcribe_copied(env_path: Path) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    result = transcribe_copied_with_fake_odin(config)
    print("Fake transcribe copied recordings")
    print(f"env_path={env_path}")
    print("odin_client=fake")
    print("delete_audio=no")
    print("write_obsidian=no")
    print(f"transcript_dir={result.transcript_dir}")
    print(f"transcribed_count={result.transcribed_count}")
    print(f"skipped_count={result.skipped_count}")
    print(f"failed_count={result.failed_count}")
    print("transcription_results:")
    for item in result.items:
        transcript_path = item.transcript_path if item.transcript_path is not None else "-"
        odin_job_id = item.odin_job_id if item.odin_job_id is not None else "-"
        print(
            f"- filename={item.job.source_filename} status={item.status} "
            f"job_id={item.job.id} odin_job_id={odin_job_id} transcript_path={transcript_path}"
        )

    return 1 if result.failed_count else 0


def _transcribe_copied(env_path: Path) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    try:
        result = transcribe_copied(config=config, client=HttpOdinClient(config.odin))
    except Exception as error:
        print(f"odin_error: {error}", file=sys.stderr)
        return 1
    print("Transcribe copied recordings")
    print(f"env_path={env_path}")
    print("odin_client=http")
    print("delete_audio=no")
    print("write_obsidian=no")
    print(f"odin_api_base_url={config.odin.api_base_url}")
    print(f"transcript_dir={result.transcript_dir}")
    print(f"transcribed_count={result.transcribed_count}")
    print(f"skipped_count={result.skipped_count}")
    print(f"failed_count={result.failed_count}")
    print("transcription_results:")
    for item in result.items:
        transcript_path = item.transcript_path if item.transcript_path is not None else "-"
        odin_job_id = item.odin_job_id if item.odin_job_id is not None else "-"
        print(
            f"- filename={item.job.source_filename} status={item.status} "
            f"job_id={item.job.id} odin_job_id={odin_job_id} transcript_path={transcript_path}"
        )

    return 1 if result.failed_count else 0


def _odin_health(env_path: Path) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    try:
        health = HttpOdinClient(config.odin, timeout_seconds=5.0).health()
    except Exception as error:
        print("Odin health")
        print(f"env_path={env_path}")
        print(f"odin_api_base_url={config.odin.api_base_url}")
        print("healthy=no")
        print(f"detail={error}")
        return 1

    print("Odin health")
    print(f"env_path={env_path}")
    print(f"odin_api_base_url={config.odin.api_base_url}")
    print(f"healthy={_yes_no(health.healthy)}")
    print(f"detail={health.detail or '-'}")
    print(f"payload_keys={','.join(sorted(health.payload.keys()))}")
    return 0 if health.healthy else 1


def _serve_odin_worker(
    env_path: Path,
    host: str,
    port: int,
    worker_root: Path | None,
) -> int:
    try:
        from logbook.odin_worker import OdinWorkerConfig, create_odin_worker_app
    except ModuleNotFoundError as error:
        print(
            f"config_error: {error.name} package is required to serve the odin worker",
            file=sys.stderr,
        )
        return 2

    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    try:
        import uvicorn
    except ImportError:
        print("config_error: uvicorn is required to serve the odin worker", file=sys.stderr)
        return 2

    root = worker_root or config.processing_root / "odin-worker"
    app = create_odin_worker_app(OdinWorkerConfig(root=root, odin=config.odin))
    uvicorn.run(app, host=host, port=port)
    return 0


def _fake_diarize_meetings(env_path: Path) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    result = diarize_meetings_with_fake_odin(config)
    _print_diarization_result(env_path, "fake", result)
    return 1 if result.failed_count else 0


def _diarize_meetings(env_path: Path) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    result = diarize_meetings(config=config, client=HttpOdinClient(config.odin))
    _print_diarization_result(env_path, "http", result)
    return 1 if result.failed_count else 0


def _print_diarization_result(env_path: Path, odin_client: str, result) -> None:
    print("Diarize meeting recordings")
    print(f"env_path={env_path}")
    print(f"odin_client={odin_client}")
    print("delete_audio=no")
    print("write_obsidian=no")
    print(f"diarization_dir={result.diarization_dir}")
    print(f"diarized_count={result.diarized_count}")
    print(f"skipped_count={result.skipped_count}")
    print(f"failed_count={result.failed_count}")
    print("diarization_results:")
    for item in result.items:
        diarization_path = item.diarization_path if item.diarization_path is not None else "-"
        odin_job_id = item.odin_job_id if item.odin_job_id is not None else "-"
        speakers = ",".join(item.speaker_labels) if item.speaker_labels else "-"
        print(
            f"- filename={item.job.source_filename} status={item.status} "
            f"job_id={item.job.id} odin_job_id={odin_job_id} "
            f"speaker_labels={speakers} diarization_path={diarization_path}"
        )


def _route_transcripts(
    env_path: Path,
    vault_root: Path,
    vault_workflow_mode: str,
    writer_mode: str,
    job_id: int | None,
    include_routed: bool,
    commit_message: str,
) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    workflow = None
    if vault_workflow_mode in {"preflight", "obsidian"} or writer_mode == "obsidian-cli":
        if config.obsidian is None:
            print("config_error: missing Obsidian configuration", file=sys.stderr)
            return 2
        preflight_workflow = ObsidianVaultWorkflow(
            config=config.obsidian,
            vault_root=vault_root,
            lock_root=config.processing_root,
        )
        preflight = preflight_workflow.preflight()
        if vault_workflow_mode == "preflight" or not preflight.operational:
            _print_vault_preflight(preflight)
        if not preflight.operational:
            return 1
        if vault_workflow_mode == "preflight":
            workflow = None
        elif vault_workflow_mode == "obsidian":
            workflow = preflight_workflow

    if writer_mode == "obsidian-cli":
        if config.obsidian is None:
            print("config_error: missing Obsidian configuration", file=sys.stderr)
            return 2
        note_writer = ObsidianCliNoteWriter(config=config.obsidian, vault_root=vault_root)
    else:
        note_writer = FilesystemNoteWriter()

    try:
        result = route_transcripts(
            config,
            vault_root=vault_root,
            vault_workflow=workflow,
            note_writer=note_writer,
            job_id=job_id,
            include_routed=include_routed,
            commit_message=commit_message,
        )
    except VaultWorkflowError as error:
        print(f"vault_workflow_error: {error}", file=sys.stderr)
        return 1

    print("Route transcribed recordings")
    print(f"env_path={env_path}")
    print(f"vault_root={result.vault_root}")
    print("delete_audio=no")
    real_vault_write = (
        config.obsidian is not None
        and writer_mode == "obsidian-cli"
        and vault_root == config.obsidian.vault_local_path
    )
    print(f"write_real_obsidian={_yes_no(real_vault_write)}")
    print(f"vault_workflow={vault_workflow_mode}")
    print(f"writer={writer_mode}")
    print(f"job_id={job_id if job_id is not None else '-'}")
    print(f"include_routed={_yes_no(include_routed)}")
    print(f"routed_count={result.routed_count}")
    print(f"log_count={result.log_count}")
    print(f"dead_letter_count={result.dead_letter_count}")
    print(f"failed_count={result.failed_count}")
    if result.vault_report is not None:
        print("vault_commands:")
        for command in result.vault_report.commands:
            status = "skipped" if command.skipped else f"exit_{command.returncode}"
            print(f"- name={command.name} status={status}")
    print("routing_results:")
    for item in result.items:
        label = item.classification.label if item.classification else "-"
        output_path = item.output_path if item.output_path is not None else "-"
        print(
            f"- filename={item.job.source_filename} status={item.status} "
            f"job_id={item.job.id} classification={label} output_path={output_path}"
        )

    return 1 if result.failed_count else 0


def _consolidate_logs(
    env_path: Path,
    vault_root: Path,
    writer_mode: str,
    entry_date: str | None,
) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    if writer_mode == "obsidian-cli":
        if config.obsidian is None:
            print("config_error: missing Obsidian configuration", file=sys.stderr)
            return 2
        workflow = ObsidianVaultWorkflow(
            config=config.obsidian,
            vault_root=vault_root,
            lock_root=config.processing_root,
        )
        preflight = workflow.preflight()
        if not preflight.operational:
            _print_vault_preflight(preflight)
            return 1
        note_writer = ObsidianCliNoteWriter(config=config.obsidian, vault_root=vault_root)
    else:
        note_writer = FilesystemNoteWriter()

    result = consolidate_daily_logs(
        config=config,
        vault_root=vault_root,
        note_writer=note_writer,
        entry_date=entry_date,
    )
    print("Consolidate daily logs")
    print(f"env_path={env_path}")
    print(f"vault_root={result.vault_root}")
    print("delete_audio=no")
    print(f"writer={writer_mode}")
    print(f"date={entry_date if entry_date is not None else '-'}")
    print(f"consolidated_count={result.consolidated_count}")
    print(f"failed_count={result.failed_count}")
    print("consolidation_results:")
    for item in result.items:
        output_path = item.daily_log_path if item.daily_log_path is not None else "-"
        print(
            f"- date={item.entry_date} status={item.status} "
            f"entry_count={item.entry_count} output_path={output_path}"
        )

    return 1 if result.failed_count else 0


def _open_log_preview(
    env_path: Path,
    vault_root: Path,
    writer_mode: str,
    entry_date: str | None,
) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    if writer_mode == "obsidian-cli":
        if config.obsidian is None:
            print("config_error: missing Obsidian configuration", file=sys.stderr)
            return 2
        workflow = ObsidianVaultWorkflow(
            config=config.obsidian,
            vault_root=vault_root,
            lock_root=config.processing_root,
        )
        preflight = workflow.preflight()
        if not preflight.operational:
            _print_vault_preflight(preflight)
            return 1
        note_writer = ObsidianCliNoteWriter(config=config.obsidian, vault_root=vault_root)
    else:
        note_writer = FilesystemNoteWriter()

    result = write_open_log_preview(
        config=config,
        vault_root=vault_root,
        note_writer=note_writer,
        entry_date=entry_date,
    )
    print("Open log preview")
    print(f"env_path={env_path}")
    print(f"vault_root={result.vault_root}")
    print("delete_audio=no")
    print(f"writer={writer_mode}")
    print(f"date={result.entry_date}")
    print(f"status={result.status}")
    print(f"entry_count={result.entry_count}")
    print(f"preview_path={result.preview_path}")
    return 1 if result.status.startswith("failed") else 0


def _link_daily_log_entities(
    env_path: Path,
    vault_root: Path | None,
    months: int,
    execute: bool,
) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2
    if config.obsidian is None and vault_root is None:
        print("config_error: missing Obsidian vault path", file=sys.stderr)
        return 2

    target_vault = vault_root or config.obsidian.vault_local_path
    try:
        result = link_daily_log_entities(
            vault_root=target_vault,
            months=months,
            execute=execute,
        )
    except ValueError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    print("Link daily log entities")
    print(f"env_path={env_path}")
    print(f"vault_root={result.vault_root}")
    print(f"months={months}")
    print(f"since={result.since}")
    print(f"until={result.until}")
    print(f"execute={_yes_no(execute)}")
    print("delete_audio=no")
    print("delete_recorder_audio=no")
    print(f"people_entities={result.people_count}")
    print(f"event_entities={result.event_count}")
    print(f"object_entities={result.object_count}")
    print(f"files_considered={result.files_considered}")
    print(f"files_changed={result.files_changed}")
    print(f"links_inserted={result.inserted_count}")
    print("entity_link_results:")
    for item in result.items:
        target_path = item.path.relative_to(result.vault_root)
        aliases = ", ".join(
            f"{link.kind}:{link.alias}->{link.target}" for link in item.links
        )
        print(
            f"- date={item.date} status={item.status} "
            f"links={item.inserted_count} path={target_path} "
            f"matches={aliases or '-'}"
        )
    return 0


def _manage_dead_letters(
    env_path: Path,
    *,
    action: str,
    job_id: int | None,
    target: str,
    vault_root: Path | None,
    linker_months: int,
    requested_by: str,
    reason: str | None,
    execute: bool,
) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    if action == "list":
        result = list_dead_letters(config)
        print("Manage dead letters")
        print(f"env_path={env_path}")
        print("action=list")
        print("execute=no")
        print(f"dead_letter_count={len(result.items)}")
        print("dead_letters:")
        for item in result.items:
            print(
                f"- job_id={item.job.id} status={item.job.status} "
                f"recorded_at={item.recorded_at.isoformat(timespec='seconds') if item.recorded_at else '-'} "
                f"obsidian_path={item.job.obsidian_path or '-'} "
                f"preview={item.text_preview or '-'}"
            )
        return 0

    if job_id is None:
        print("config_error: --job-id is required for assign, rescue, and discard", file=sys.stderr)
        return 2

    target_vault = vault_root
    if action in {"assign", "rescue"} and target_vault is None:
        if config.obsidian is None:
            print("config_error: missing Obsidian vault path", file=sys.stderr)
            return 2
        target_vault = config.obsidian.vault_local_path

    if action == "assign":
        if target != "log":
            print("config_error: only --target log is supported", file=sys.stderr)
            return 2
        result = assign_dead_letter_to_log(
            config=config,
            vault_root=target_vault,
            job_id=job_id,
            execute=execute,
            requested_by=requested_by,
            reason=reason,
            linker_months=linker_months,
        )
    elif action == "rescue":
        if target != "meeting":
            print("config_error: rescue currently supports only --target meeting", file=sys.stderr)
            return 2
        result = rescue_dead_letter_as_meeting(
            config=config,
            vault_root=target_vault,
            job_id=job_id,
            execute=execute,
            requested_by=requested_by,
            reason=reason,
            client=HttpOdinClient(config.odin),
        )
    elif action == "discard":
        result = discard_dead_letter(
            config=config,
            job_id=job_id,
            execute=execute,
            requested_by=requested_by,
            reason=reason,
        )
    else:
        print(f"config_error: unsupported action {action}", file=sys.stderr)
        return 2

    print("Manage dead letters")
    print(f"env_path={env_path}")
    print(f"action={action}")
    print(f"job_id={job_id}")
    print(f"target={target if action in {'assign', 'rescue'} else '-'}")
    print(f"execute={_yes_no(execute)}")
    print("delete_audio=no")
    print("delete_recorder_audio=no")
    if target_vault is not None:
        print(f"vault_root={target_vault}")
    print(f"status={result.status}")
    print(f"blockers={','.join(result.blockers) if result.blockers else '-'}")
    print(f"audit_id={result.audit.id if result.audit is not None else '-'}")
    print(f"inbox_path={result.inbox_path if result.inbox_path is not None else '-'}")
    print(f"meeting_path={result.meeting_path if result.meeting_path is not None else '-'}")
    print(
        "removed_dead_letter_path="
        f"{result.removed_dead_letter_path if result.removed_dead_letter_path is not None else '-'}"
    )
    print(f"daily_log_path={result.daily_log_path if result.daily_log_path is not None else '-'}")
    if result.diarization is not None:
        print(f"diarized_count={result.diarization.diarized_count}")
        print(f"diarization_failed_count={result.diarization.failed_count}")
    if result.routing is not None:
        print(f"routed_count={result.routing.routed_count}")
        print(f"routing_failed_count={result.routing.failed_count}")
    if result.consolidation is not None:
        print(f"consolidated_count={result.consolidation.consolidated_count}")
        print(f"consolidation_failed_count={result.consolidation.failed_count}")
    if result.entity_links is not None:
        print(f"entity_link_files_changed={result.entity_links.files_changed}")
        print(f"entity_links_inserted={result.entity_links.inserted_count}")
    return 1 if result.status.startswith(("blocked", "failed")) else 0


def _vault_preflight(env_path: Path, vault_root: Path | None) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2
    if config.obsidian is None:
        print("config_error: missing Obsidian configuration", file=sys.stderr)
        return 2

    target_vault = vault_root or config.obsidian.vault_local_path
    workflow = ObsidianVaultWorkflow(
        config=config.obsidian,
        vault_root=target_vault,
        lock_root=config.processing_root,
    )
    preflight = workflow.preflight()
    print("Obsidian vault preflight")
    print(f"env_path={env_path}")
    print(f"vault_root={target_vault}")
    print("write_files=no")
    print("run_sync=no")
    _print_vault_preflight(preflight)
    return 0 if preflight.operational else 1


def _mark_vault_synced(env_path: Path, execute: bool) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    try:
        result = mark_vault_synced_jobs(config, dry_run=not execute)
    except ValueError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    print("Mark vault synced")
    print(f"env_path={env_path}")
    print(f"vault_root={result.vault_root}")
    print(f"execute={_yes_no(execute)}")
    print("delete_audio=no")
    print("delete_recorder_audio=no")
    print(f"vault_head={result.vault_head or '-'}")
    print(f"origin_main={result.origin_head or '-'}")
    _print_vault_preflight(result.preflight)
    print(f"jobs_considered={len(result.items)}")
    print(f"markable_count={result.markable_count}")
    print(f"marked_count={result.marked_count}")
    print(f"already_synced_count={result.already_synced_count}")
    print(f"blocked_count={result.blocked_count}")
    print("vault_sync_results:")
    for item in result.items:
        blockers = ",".join(item.blockers) if item.blockers else "-"
        paths = ",".join(item.paths) if item.paths else "-"
        print(
            f"- job_id={item.job.id} status={item.job.status} "
            f"sync_status={item.status} paths={paths} blockers={blockers}"
        )
    return 1 if result.blocked_count else 0


def _extract_insights(
    env_path: Path,
    vault_root: Path,
    writer_mode: str,
    job_id: int | None,
) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    if writer_mode == "obsidian-cli":
        if config.obsidian is None:
            print("config_error: missing Obsidian configuration", file=sys.stderr)
            return 2
        workflow = ObsidianVaultWorkflow(
            config=config.obsidian,
            vault_root=vault_root,
            lock_root=config.processing_root,
        )
        preflight = workflow.preflight()
        if not preflight.operational:
            _print_vault_preflight(preflight)
            return 1
        note_writer = ObsidianCliNoteWriter(config=config.obsidian, vault_root=vault_root)
    else:
        note_writer = FilesystemNoteWriter()

    result = extract_insights(
        config=config,
        vault_root=vault_root,
        note_writer=note_writer,
        job_id=job_id,
    )
    print("Extract insights")
    print(f"env_path={env_path}")
    print(f"vault_root={result.vault_root}")
    print("delete_audio=no")
    print("write_canonical_notes=no")
    print(f"writer={writer_mode}")
    print(f"job_id={job_id if job_id is not None else '-'}")
    print(f"artifact_dir={result.artifact_dir}")
    print(f"extracted_count={result.extracted_count}")
    print(f"skipped_count={result.skipped_count}")
    print(f"failed_count={result.failed_count}")
    print("insight_results:")
    for item in result.items:
        artifact_path = item.artifact_path if item.artifact_path is not None else "-"
        review_note_path = item.review_note_path if item.review_note_path is not None else "-"
        print(
            f"- filename={item.job.source_filename} status={item.status} "
            f"job_id={item.job.id} action_count={len(item.action_items)} "
            f"artifact_path={artifact_path} review_note_path={review_note_path}"
        )
    return 1 if result.failed_count else 0


def _memory_graph_sync(env_path: Path, job_id: int | None, execute: bool) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    plan = build_memory_graph_plan(config, job_id=job_id)
    result = None
    if execute:
        if config.memgraph is None:
            print("config_error: missing MEMGRAPH_URI", file=sys.stderr)
            return 2
        try:
            result = apply_memory_graph_plan(plan, Neo4jMemgraphClient(config.memgraph))
        except RuntimeError as error:
            print(f"config_error: {error}", file=sys.stderr)
            return 2

    print("Memory graph sync")
    print(f"env_path={env_path}")
    print(f"job_id={job_id if job_id is not None else '-'}")
    print(f"execute={_yes_no(execute)}")
    print("delete_audio=no")
    print("delete_recorder_audio=no")
    print(f"memgraph_uri={config.memgraph.uri if config.memgraph is not None else '-'}")
    print(f"nodes_planned={plan.node_count}")
    print(f"relationships_planned={plan.relationship_count}")
    print(f"nodes_written={result.nodes_written if result is not None else 0}")
    print(
        "relationships_written="
        f"{result.relationships_written if result is not None else 0}"
    )
    print("counts_by_label:")
    for label, count in plan.counts_by_label.items():
        print(f"- label={label} count={count}")
    print("counts_by_relationship:")
    for relationship_type, count in plan.counts_by_relationship.items():
        print(f"- type={relationship_type} count={count}")
    return 0


def _memory_graph_query(env_path: Path, query: str, job_id: int | None) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    plan = build_memory_graph_plan(config, job_id=job_id)
    rows = query_memory_graph_plan(plan, query)
    print("Memory graph query")
    print(f"env_path={env_path}")
    print(f"job_id={job_id if job_id is not None else '-'}")
    print(f"query={query}")
    print(f"result_count={len(rows)}")
    print("results:")
    for row in rows:
        rendered = " ".join(
            f"{key}={value if value not in (None, '') else '-'}"
            for key, value in sorted(row.items())
        )
        print(f"- {rendered}")
    return 0


def _memory_graph_health(env_path: Path) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    health = check_memory_graph_health(config)
    print("Memory graph health")
    print(f"env_path={env_path}")
    print("delete_audio=no")
    print("delete_recorder_audio=no")
    print(f"memgraph_uri={config.memgraph.uri if config.memgraph is not None else '-'}")
    print(f"status={health.status}")
    print(f"reachable={_yes_no(health.reachable)}")
    print(f"detail={health.detail or '-'}")
    print(f"planned_nodes={health.planned_nodes}")
    print(f"live_nodes={health.live_nodes if health.live_nodes is not None else '-'}")
    print(f"planned_relationships={health.planned_relationships}")
    print(
        "live_relationships="
        f"{health.live_relationships if health.live_relationships is not None else '-'}"
    )
    print("counts_by_label:")
    for label in sorted(
        set(health.planned_counts_by_label) | set(health.live_counts_by_label)
    ):
        planned = health.planned_counts_by_label.get(label, 0)
        live = health.live_counts_by_label.get(label)
        drift = health.drift_by_label.get(label)
        print(
            f"- label={label} planned={planned} "
            f"live={live if live is not None else '-'} "
            f"drift={drift if drift is not None else '-'}"
        )
    print("counts_by_relationship:")
    for relationship_type in sorted(
        set(health.planned_counts_by_relationship)
        | set(health.live_counts_by_relationship)
    ):
        planned = health.planned_counts_by_relationship.get(relationship_type, 0)
        live = health.live_counts_by_relationship.get(relationship_type)
        drift = health.drift_by_relationship.get(relationship_type)
        print(
            f"- type={relationship_type} planned={planned} "
            f"live={live if live is not None else '-'} "
            f"drift={drift if drift is not None else '-'}"
        )
    return 1 if health.status in {"drift", "unavailable"} else 0


def _memory_graph_repair(env_path: Path, prune_stale: bool, execute: bool) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2
    if config.memgraph is None:
        print("config_error: missing MEMGRAPH_URI", file=sys.stderr)
        return 2

    plan = build_memory_graph_plan(config)
    try:
        repair_plan = build_memory_graph_repair_plan(
            plan,
            Neo4jMemgraphClient(config.memgraph),
        )
    except RuntimeError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2
    before_status = _memory_graph_repair_status(repair_plan)
    result = None
    after_status = "not_checked"
    if execute:
        try:
            result = apply_memory_graph_repair_plan(
                repair_plan,
                Neo4jMemgraphClient(config.memgraph),
                prune_stale=prune_stale,
            )
            after_plan = build_memory_graph_repair_plan(
                plan,
                Neo4jMemgraphClient(config.memgraph),
            )
        except RuntimeError as error:
            print(f"config_error: {error}", file=sys.stderr)
            return 2
        after_status = _memory_graph_repair_status(after_plan)

    print("Memory graph repair")
    print(f"env_path={env_path}")
    print(f"execute={_yes_no(execute)}")
    print(f"prune_stale={_yes_no(prune_stale)}")
    print("delete_audio=no")
    print("delete_recorder_audio=no")
    print(f"memgraph_uri={config.memgraph.uri}")
    print(f"before_status={before_status}")
    print(f"after_status={after_status}")
    print(f"planned_nodes={plan.node_count}")
    print(f"planned_relationships={plan.relationship_count}")
    print(f"live_nodes={len(repair_plan.live_node_ids)}")
    print(f"live_relationships={len(repair_plan.live_relationship_ids)}")
    print(f"missing_nodes={len(repair_plan.missing_nodes)}")
    print(f"missing_relationships={len(repair_plan.missing_relationships)}")
    print(f"stale_nodes={len(repair_plan.stale_node_ids)}")
    print(f"stale_relationships={len(repair_plan.stale_relationship_ids)}")
    print(f"nodes_written={result.nodes_written if result is not None else 0}")
    print(
        "relationships_written="
        f"{result.relationships_written if result is not None else 0}"
    )
    print(f"nodes_pruned={result.nodes_pruned if result is not None else 0}")
    print(
        "relationships_pruned="
        f"{result.relationships_pruned if result is not None else 0}"
    )
    _print_id_preview("missing_node_ids", [node.id for node in repair_plan.missing_nodes])
    _print_id_preview(
        "missing_relationship_ids",
        [relationship.id for relationship in repair_plan.missing_relationships],
    )
    _print_id_preview("stale_node_ids", list(repair_plan.stale_node_ids))
    _print_id_preview("stale_relationship_ids", list(repair_plan.stale_relationship_ids))
    return 0 if after_status in {"ok", "not_checked"} else 1


def _memory_graph_repair_status(repair_plan) -> str:
    has_drift = (
        repair_plan.missing_nodes
        or repair_plan.missing_relationships
        or repair_plan.stale_node_ids
        or repair_plan.stale_relationship_ids
    )
    return "drift" if has_drift else "ok"


def _print_id_preview(label: str, ids: list[str], limit: int = 20) -> None:
    print(f"{label}:")
    for item in ids[:limit]:
        print(f"- {item}")
    if len(ids) > limit:
        print(f"- ... {len(ids) - limit} more")
    if not ids:
        print("- -")


def _memory_action_resolve(
    env_path: Path,
    action_id: str,
    resolved_by: str,
    note: str | None,
    execute: bool,
) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    plan = build_memory_graph_plan(config)
    candidate = next(
        (
            node
            for node in plan.nodes
            if node.id == action_id and "ActionCandidate" in node.labels
        ),
        None,
    )
    if candidate is None:
        print("memory_action_error: action candidate not found", file=sys.stderr)
        return 1

    review_status = str(candidate.properties.get("review_status") or "needs_review")
    resolved_at = "-"
    if execute:
        ledger = open_ledger(config.sqlite_path, initialize=True)
        try:
            review = ledger.resolve_memory_action(
                action_id=action_id,
                resolved_by=resolved_by,
                resolution_note=note,
            )
            ledger.record_action(
                action_type="memory.action.resolve",
                target_type="memory_action",
                target_id=action_id,
                request_payload={
                    "reason": note,
                    "job_id": candidate.properties.get("job_id"),
                    "text": candidate.properties.get("text"),
                },
                requested_by=resolved_by,
            )
        finally:
            ledger.close()
        review_status = review.review_status
        resolved_at = review.resolved_at or "-"

    print("Memory action resolve")
    print(f"env_path={env_path}")
    print(f"action_id={action_id}")
    print(f"execute={_yes_no(execute)}")
    print("delete_audio=no")
    print("delete_recorder_audio=no")
    print(f"job_id={candidate.properties.get('job_id') or '-'}")
    print(f"previous_review_status={candidate.properties.get('review_status') or '-'}")
    print(f"review_status={review_status if execute else 'would_resolve'}")
    print(f"resolved_by={resolved_by}")
    print(f"resolved_at={resolved_at}")
    print(f"text={candidate.properties.get('text') or '-'}")
    return 0


def _serve_api(env_path: Path, host: str | None, port: int | None) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    from logbook.api import create_app

    try:
        import uvicorn
    except ImportError:
        print("config_error: uvicorn is required to serve the FastAPI app", file=sys.stderr)
        return 2

    bind_host = host or (config.api.bind_host if config.api is not None else "127.0.0.1")
    bind_port = port or (config.api.port if config.api is not None else 8765)

    app = create_app(config)
    uvicorn.run(app, host=bind_host, port=bind_port)
    return 0


def _watch_web(env_path: Path, host: str, port: int) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    from logbook.watch_web import create_watch_web_app

    try:
        import uvicorn
    except ImportError:  # pragma: no cover - dependency is validated by the quality gate
        print("config_error: uvicorn is required to serve the watcher web UI", file=sys.stderr)
        return 2

    app = create_watch_web_app(config)
    print(f"Logbook Watch web UI: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)
    return 0


def _watch(
    env_path: Path,
    *,
    api_url: str | None,
    read_token_env: str | None,
    once: bool,
    json_output: bool,
    refresh_interval: float,
    max_refreshes: int | None,
    theme: str,
    no_color: bool,
    ui: str,
    status_filter: str,
    fail_on: str,
) -> int:
    if json_output and not once and max_refreshes is None:
        print("config_error: live JSON output requires --once or --max-refreshes", file=sys.stderr)
        return 2

    try:
        config = None if api_url else load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    refreshes = 1 if once else max_refreshes
    if ui == "curses" and not json_output:
        from logbook.watch_curses import render_curses_frame, run_curses_watch

        def snapshot_provider():
            snapshot = (
                _fetch_observer_snapshot(api_url, read_token_env)
                if api_url
                else build_observer_snapshot(config, probe_services=True)
            )
            return snapshot

        def recorder_status_provider(snapshot):
            if config is None:
                return None
            return _curses_recorder_status(config, snapshot)

        if once:
            snapshot = filter_observer_snapshot(snapshot_provider(), status_filter)
            terminal = shutil.get_terminal_size((100, 30))
            frame = render_curses_frame(
                snapshot,
                width=terminal.columns,
                height=terminal.lines,
                theme=theme,
                status_filter=status_filter,
                refresh_interval=refresh_interval,
                recorder_status=recorder_status_provider(snapshot),
            )
            print(frame.text(), end="")
            return _watch_exit_code(snapshot, fail_on)
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            print("config_error: --ui curses requires an interactive terminal", file=sys.stderr)
            return 2
        return run_curses_watch(  # pragma: no cover - requires an interactive terminal
            snapshot_provider,
            refresh_interval=refresh_interval,
            theme=theme,
            status_filter=status_filter,
            fail_on=lambda snapshot: _watch_exit_code(snapshot, fail_on),
            recorder_status_provider=recorder_status_provider if config is not None else None,
            eject_recorder=lambda: _eject_recorder(config) if config is not None else (False, "remote watch"),
        )

    interactive_full_ui = (
        ui == "full"
        and not once
        and not json_output
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    )
    terminal_settings = termios.tcgetattr(sys.stdin) if interactive_full_ui else None
    if interactive_full_ui:
        tty.setcbreak(sys.stdin.fileno())

    count = 0
    last_code = 0
    try:
        while True:
            snapshot = (
                _fetch_observer_snapshot(api_url, read_token_env)
                if api_url
                else build_observer_snapshot(config, probe_services=True)
            )
            snapshot = filter_observer_snapshot(snapshot, status_filter)
            last_code = _watch_exit_code(snapshot, fail_on)
            if json_output:
                print(json.dumps(snapshot.to_dict(), indent=2, sort_keys=True))
            else:
                if not once:
                    print("\033[2J\033[H", end="")
                if ui == "full":
                    terminal = shutil.get_terminal_size((100, 30))
                    rendered = render_full_observer_dashboard(
                        snapshot,
                        theme=theme,
                        color=not no_color and sys.stdout.isatty(),
                        width=terminal.columns,
                        height=terminal.lines,
                    )
                else:
                    rendered = render_observer_snapshot(
                        snapshot,
                        theme=theme,
                        color=not no_color and sys.stdout.isatty(),
                    )
                print(
                    rendered,
                    end="",
                )
            count += 1
            if refreshes is not None and count >= refreshes:
                return last_code
            if once:
                return last_code
            try:
                key = (
                    _read_watch_key(refresh_interval)
                    if interactive_full_ui
                    else _sleep_for_watch_interval(refresh_interval)
                )
            except KeyboardInterrupt:
                return last_code
            if key == "q":
                return last_code
            if key == "f":
                status_filter = "failed"
            elif key == "a":
                status_filter = "all"
            elif key == "+":
                refresh_interval = max(0.25, refresh_interval / 2)
            elif key == "-":
                refresh_interval = min(60.0, refresh_interval * 2)
    finally:
        if terminal_settings is not None:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, terminal_settings)


def _sleep_for_watch_interval(refresh_interval: float) -> None:
    time.sleep(max(0.0, refresh_interval))
    return None


def _read_watch_key(refresh_interval: float) -> str | None:
    readable, _, _ = select.select([sys.stdin], [], [], max(0.0, refresh_interval))
    if not readable:
        return None
    return sys.stdin.read(1).lower()


def _fetch_observer_snapshot(api_url: str, read_token_env: str | None):
    base_url = api_url.rstrip("/")
    headers = {}
    if read_token_env:
        token = os.environ.get(read_token_env)
        if token:
            headers["Authorization"] = f"Bearer {token}"
    http_request = request.Request(f"{base_url}/observer/snapshot", headers=headers)
    with request.urlopen(http_request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return observer_snapshot_from_dict(payload)


def _curses_recorder_status(config: AppConfig, snapshot):
    from logbook.watch_curses import CursesRecorderStatus

    validation = validate_recorder(config.recorder)
    mounted = validation.operational
    blocked_reason = None
    if mounted and snapshot.current_run is not None:
        blocked_reason = "pipeline running"
    return CursesRecorderStatus(
        mounted=mounted,
        volume_name=validation.volume_name if mounted else validation.expected_volume_name,
        writable=validation.writable if mounted else None,
        eject_available=mounted and blocked_reason is None,
        blocked_reason=blocked_reason,
    )


def _eject_recorder(config: AppConfig) -> tuple[bool, str]:
    validation = validate_recorder(config.recorder)
    if not validation.operational:
        return False, "recorder not mounted"
    result = subprocess.run(
        ["diskutil", "eject", str(validation.resolved_mount_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    detail = (result.stdout or result.stderr or "").strip()
    if not detail:
        detail = validation.volume_name
    return result.returncode == 0, detail


def _watch_exit_code(snapshot, fail_on: str) -> int:
    has_failure = bool(snapshot.recent_failures)
    is_stale = bool(snapshot.current_run and snapshot.current_run.get("stale"))
    if fail_on == "failure" and has_failure:
        return 1
    if fail_on == "stale" and is_stale:
        return 1
    if fail_on == "any" and (has_failure or is_stale):
        return 1
    return 0


def _retention_status(env_path: Path) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    print("Audio retention status")
    print(f"env_path={env_path}")
    print(f"retention_hours={config.retention.hours}")
    print(f"cleanup_mode={config.retention.cleanup_mode}")
    plan = plan_audio_cleanup(config)
    print("cleanup_implementation=LGB-026")
    print(f"jobs_considered={len(plan.items)}")
    print(f"eligible_count={plan.eligible_count}")
    print(f"blocked_count={plan.blocked_count}")
    print(f"local_pending_count={plan.local_pending_count}")
    print(f"recorder_pending_count={plan.recorder_pending_count}")
    print("delete_audio=no")
    print("delete_recorder_audio=no")
    return 0


def _cleanup_plan(env_path: Path) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    plan = plan_audio_cleanup(config)
    _print_cleanup_plan(env_path, plan, execute=False, include_recorder=False)
    return 0


def _cleanup_audio(env_path: Path, execute: bool, include_recorder: bool) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    if execute:
        plan = execute_audio_cleanup(config, include_recorder=include_recorder)
    else:
        plan = plan_audio_cleanup(config)
    _print_cleanup_plan(env_path, plan, execute=execute, include_recorder=include_recorder)
    return 0


def _print_cleanup_plan(env_path: Path, plan, execute: bool, include_recorder: bool) -> None:
    print("Audio cleanup plan")
    print(f"env_path={env_path}")
    print(f"retention_hours={plan.retention_hours}")
    print(f"execute={_yes_no(execute)}")
    print(f"include_recorder={_yes_no(include_recorder)}")
    print(f"jobs_considered={len(plan.items)}")
    print(f"eligible_count={plan.eligible_count}")
    print(f"blocked_count={plan.blocked_count}")
    print(f"local_pending_count={plan.local_pending_count}")
    print(f"recorder_pending_count={plan.recorder_pending_count}")
    print("cleanup_results:")
    for item in plan.items:
        blockers = ",".join(item.blockers) if item.blockers else "-"
        print(
            f"- job_id={item.job.id} status={item.job.status} eligible={_yes_no(item.eligible)} "
            f"cleanup_eligible_at={item.cleanup_eligible_at or '-'} "
            f"local_action={item.local_action} recorder_action={item.recorder_action} "
            f"blockers={blockers}"
        )


def _launchd_render(
    env_path: Path,
    output_dir: Path | None,
    repo_root: Path,
    python_bin: str,
) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2

    target_output_dir = output_dir or config.processing_root / "launchd"
    package = render_launchd_package(
        config=config,
        env_path=env_path,
        output_dir=target_output_dir,
        repo_root=repo_root,
        python_bin=python_bin,
    )
    paths = write_launchd_package(package)

    print("Render launchd package")
    print(f"env_path={env_path}")
    print(f"repo_root={repo_root}")
    print(f"output_dir={target_output_dir}")
    print(f"python_bin={python_bin}")
    print("load_launchd=no")
    print("start_openclaw=no")
    print("mount_trigger_command=process-mounted-recorder")
    print(f"mount_runner_app={package.mount_runner.bundle_path}")
    print("retention_cleanup_command=cleanup-audio --execute --include-recorder")
    print("plists:")
    for path in paths:
        print(f"- {path}")
    return 0


def _backup_run(
    env_path: Path,
    backup_root: str | None,
    repo_root: Path,
    execute: bool,
) -> int:
    try:
        config = load_app_config(env_path)
    except ConfigError as error:
        print(f"config_error: {error}", file=sys.stderr)
        return 2
    target_root = backup_root or (config.backup.root if config.backup is not None else None)
    if target_root is None:
        print("config_error: missing backup root", file=sys.stderr)
        return 2

    try:
        result = run_backup(
            config=config,
            repo_root=repo_root,
            env_path=env_path,
            backup_root=target_root,
            execute=execute,
            ssh_identity_file=(
                config.backup.ssh_identity_file if config.backup is not None else None
            ),
        )
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"backup_error: {error}", file=sys.stderr)
        return 1

    print("Logbook backup")
    print(f"env_path={env_path}")
    print(f"repo_root={repo_root}")
    print(f"backup_root={result.backup_root}")
    print(f"backup_id={result.backup_id}")
    print(f"backup_dir={result.backup_dir}")
    print(f"remote_target={result.remote_target or '-'}")
    print(f"execute={_yes_no(result.executed)}")
    print("delete_audio=no")
    print("delete_recorder_audio=no")
    print(f"audio_policy={result.audio_policy}")
    print(f"secret_policy={result.secret_policy}")
    print(f"ledger_job_count={result.ledger_job_count}")
    print(f"action_audit_count={result.action_audit_count}")
    print(f"planned_artifact_count={len(result.planned_relative_paths)}")
    print(f"copied_artifact_count={len(result.copied_relative_paths)}")
    print(f"manifest_path={result.manifest_path if result.executed else '-'}")
    print("backup_artifacts:")
    paths = result.copied_relative_paths if result.executed else result.planned_relative_paths
    for path in paths:
        print(f"- {path}")
    return 0


def _backup_restore_drill(env_path: Path, backup_location: str) -> int:
    try:
        config = load_app_config(env_path)
        result = run_restore_drill(
            backup_location,
            ssh_identity_file=(
                config.backup.ssh_identity_file if config.backup is not None else None
            ),
        )
    except (ConfigError, OSError, ValueError, sqlite3.Error, subprocess.CalledProcessError) as error:
        print(f"restore_drill_error: {error}", file=sys.stderr)
        return 1

    print("Logbook restore drill")
    print(f"backup_dir={result.backup_dir}")
    print(f"manifest_path={result.manifest_path}")
    print(f"ledger_path={result.ledger_path}")
    print("write_production=no")
    print("delete_audio=no")
    print(f"status={result.status}")
    print(f"integrity_check={result.integrity_check}")
    print(f"schema_version={result.schema_version if result.schema_version is not None else '-'}")
    print(f"expected_job_count={result.expected_job_count if result.expected_job_count is not None else '-'}")
    print(f"job_count={result.job_count}")
    print(f"action_audit_count={result.action_audit_count}")
    return 0 if result.status == "ok" else 1


def _print_vault_preflight(preflight) -> None:
    print(f"operational={_yes_no(preflight.operational)}")
    print("checks:")
    for check in preflight.checks:
        print(f"- name={check.name} ok={_yes_no(check.ok)} detail={check.detail}")


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _match_label(value: bool | None) -> str:
    if value is None:
        return "unknown"
    return "yes" if value else "no"


if __name__ == "__main__":
    raise SystemExit(main())
