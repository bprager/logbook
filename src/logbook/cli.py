from __future__ import annotations

import argparse
import sys
from pathlib import Path

from logbook.config import ConfigError, load_app_config, load_recorder_config
from logbook.consolidation import consolidate_daily_logs
from logbook.copying import copy_discovered_recordings
from logbook.ingest import run_ingest_dry_run
from logbook.launchd import render_launchd_package, write_launchd_package
from logbook.recorder import discover_recordings, validate_recorder
from logbook.routing import route_transcripts
from logbook.transcription import transcribe_copied_with_fake_odin
from logbook.vault import ObsidianVaultWorkflow, VaultWorkflowError
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

    fake_transcribe_parser = subparsers.add_parser(
        "fake-transcribe-copied",
        help="exercise the odin client boundary with fake transcripts for copied files",
    )
    fake_transcribe_parser.add_argument("--env", type=Path, default=Path(".env"))

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

    serve_api_parser = subparsers.add_parser(
        "serve-api",
        help="start the read-only FastAPI status API",
    )
    serve_api_parser.add_argument("--env", type=Path, default=Path(".env"))
    serve_api_parser.add_argument("--host", default=None)
    serve_api_parser.add_argument("--port", type=int, default=None)

    retention_status_parser = subparsers.add_parser(
        "retention-status",
        help="report retention cleanup configuration without deleting audio",
    )
    retention_status_parser.add_argument("--env", type=Path, default=Path(".env"))

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

    args = parser.parse_args(argv)

    if args.command == "recorder-discover":
        return _recorder_discover(args.env)
    if args.command == "ingest-dry-run":
        return _ingest_dry_run(args.env, record_discovery=args.record_discovery)
    if args.command == "copy-discovered":
        return _copy_discovered(args.env)
    if args.command == "fake-transcribe-copied":
        return _fake_transcribe_copied(args.env)
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
    if args.command == "vault-preflight":
        return _vault_preflight(args.env, args.vault)
    if args.command == "serve-api":
        return _serve_api(args.env, args.host, args.port)
    if args.command == "retention-status":
        return _retention_status(args.env)
    if args.command == "launchd-render":
        return _launchd_render(args.env, args.output_dir, args.repo_root, args.python_bin)

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

    recordings = discover_recordings(validation.recordings_dir)
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
    if not validation.operational:
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
    print("cleanup_implementation=LGB-026")
    print("delete_audio=no")
    print("delete_recorder_audio=no")
    return 0


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
    print("mount_trigger_command=recorder-discover")
    print("retention_delete_audio=no")
    print("plists:")
    for path in paths:
        print(f"- {path}")
    return 0


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
