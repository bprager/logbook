from __future__ import annotations

import io
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from contextlib import redirect_stdout
from contextlib import redirect_stderr
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from logbook.cli import _curses_recorder_status, _eject_recorder, main
from logbook.config import load_app_config
from logbook.ledger import open_ledger
from logbook.observer import (
    build_observer_snapshot,
    observer_snapshot_from_dict,
    format_observer_timestamp,
    render_full_observer_dashboard,
    render_observer_snapshot,
    resolve_watch_theme,
    _failed_pipeline_runs,
)
from logbook.recorder import discover_recordings
from logbook.telemetry import SQLitePipelineReporter
from logbook.watch_curses import (
    CursesRecorderStatus,
    _draw_frame,
    _is_curses_quit_key,
    _read_key,
    _should_eject_recorder,
    render_curses_frame,
)


MB = 1024 * 1024


class ObserverSnapshotTests(TestCase):
    def test_snapshot_reports_recent_outcomes_and_stats_without_paths(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            seeded = _seed_observer_fixture(config)

            snapshot = build_observer_snapshot(
                config,
                generated_at=datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(snapshot.health.sqlite, "ok")
            self.assertEqual(snapshot.health.odin, "unknown")
            self.assertEqual(snapshot.health.memgraph, "not_configured")
            self.assertIsNone(snapshot.current_run)
            self.assertIsNone(snapshot.active_stage)
            self.assertEqual(snapshot.latest_finished_at, "2026-05-15T11:12:00+00:00")
            self.assertEqual(snapshot.stats.jobs_seen, 3)
            self.assertEqual(snapshot.stats.succeeded, 1)
            self.assertEqual(snapshot.stats.failed, 1)
            self.assertEqual(snapshot.stats.dead_letters, 1)
            self.assertEqual(snapshot.stats.p50_duration_seconds, 1800)
            self.assertEqual(snapshot.stats.p90_duration_seconds, 2700)
            self.assertEqual(
                [item.job_id for item in snapshot.recent_finished],
                [seeded["dead_letter_id"], seeded["consolidated_id"]],
            )
            self.assertEqual(snapshot.recent_failures[0].job_id, seeded["failed_id"])
            self.assertEqual(snapshot.recent_failures[0].safe_detail, "failed_transcription")

            payload = json.dumps(snapshot.to_dict(), sort_keys=True)
            self.assertNotIn("source_path", payload)
            self.assertNotIn("copied_path", payload)
            self.assertNotIn("transcript_path", payload)
            self.assertNotIn(str(config.processing_root), payload)
            self.assertNotIn(str(config.recorder.mount_path), payload)

    def test_snapshot_payload_exports_watch_timestamps_in_local_time(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            _seed_observer_fixture(config)
            snapshot = build_observer_snapshot(
                config,
                generated_at=datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
            )

            with _local_timezone("America/Los_Angeles"):
                payload = snapshot.to_dict()

            self.assertEqual(payload["generated_at"], "2026-05-15T05:00:00-07:00")
            self.assertEqual(payload["latest_finished_at"], "2026-05-15T04:12:00-07:00")
            self.assertEqual(
                payload["recent_finished"][0]["finished_at"],
                "2026-05-15T03:45:00-07:00",
            )
            self.assertEqual(
                payload["recent_finished"][0]["recorded_at"],
                "2026-05-15T10:00:00",
            )
            self.assertEqual(
                payload["recent_failures"][0]["occurred_at"],
                "2026-05-15T04:12:00-07:00",
            )

    def test_plain_render_is_compact_and_path_safe(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            _seed_observer_fixture(config)
            snapshot = build_observer_snapshot(
                config,
                generated_at=datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
            )

            with _local_timezone("America/Los_Angeles"):
                rendered = render_observer_snapshot(snapshot)

            self.assertIn("Logbook 2026-05-15T05:00:00-07:00", rendered)
            self.assertIn("Latest finished job 2026-05-15T04:12:00-07:00", rendered)
            self.assertIn("Run none", rendered)
            self.assertIn("Recent finished", rendered)
            self.assertIn("Failures and review", rendered)
            self.assertIn("Stats 24h  jobs 3  ok 1  dead_letters 1  failed 1", rendered)
            self.assertNotIn(str(config.processing_root), rendered)
            self.assertNotIn(str(config.recorder.mount_path), rendered)
            self.assertTrue(all(len(line) <= 100 for line in rendered.splitlines()))

    def test_watch_once_outputs_plain_text_and_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            _seed_observer_fixture(config)

            def fixed_snapshot(config, probe_services=False):
                return build_observer_snapshot(
                    config,
                    generated_at=datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
                    probe_services=probe_services,
                )

            plain = io.StringIO()
            with patch("logbook.cli.build_observer_snapshot", side_effect=fixed_snapshot):
                with redirect_stdout(plain):
                    plain_code = main(["watch", "--env", str(env_path), "--once"])

            with patch("logbook.cli.build_observer_snapshot", side_effect=fixed_snapshot):
                as_json = io.StringIO()
                with redirect_stdout(as_json):
                    json_code = main(["watch", "--env", str(env_path), "--once", "--json"])

            self.assertEqual(plain_code, 0)
            self.assertIn("Recent finished", plain.getvalue())
            self.assertIn("Stats 24h", plain.getvalue())
            self.assertEqual(json_code, 0)
            payload = json.loads(as_json.getvalue())
            self.assertEqual(payload["stats"]["jobs_seen"], 3)
            self.assertEqual(payload["recent_failures"][0]["safe_detail"], "failed_transcription")

    def test_watch_once_supports_day_theme_no_color_filter_and_failure_exit(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            _seed_observer_fixture(config)

            def fixed_snapshot(config, probe_services=False):
                return build_observer_snapshot(
                    config,
                    generated_at=datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
                    probe_services=probe_services,
                )

            plain = io.StringIO()
            with patch("logbook.cli.build_observer_snapshot", side_effect=fixed_snapshot):
                with redirect_stdout(plain):
                    code = main(
                        [
                            "watch",
                            "--env",
                            str(env_path),
                            "--once",
                            "--theme",
                            "day",
                            "--no-color",
                            "--status",
                            "failed",
                            "--fail-on",
                            "failure",
                        ]
                    )

            output = plain.getvalue()
            self.assertEqual(code, 1)
            self.assertIn("view day", output)
            self.assertIn("fail  #", output)
            self.assertIn("failed_transcription", output)
            self.assertNotIn("ok  #", output)
            self.assertNotIn("\x1b[", output)

    def test_watch_live_refresh_clears_terminal_and_respects_max_refreshes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            _seed_observer_fixture(config)

            plain = io.StringIO()
            with redirect_stdout(plain):
                code = main(
                    [
                        "watch",
                        "--env",
                        str(env_path),
                        "--refresh-interval",
                        "0",
                        "--max-refreshes",
                        "2",
                        "--no-color",
                    ]
                )

            output = plain.getvalue()
            self.assertEqual(code, 0)
            self.assertEqual(output.count("\x1b[2J\x1b[H"), 2)
            self.assertEqual(output.count("Logbook "), 2)

    def test_full_terminal_dashboard_is_compact_path_safe_and_operator_focused(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            _seed_observer_fixture(config)
            snapshot = build_observer_snapshot(
                config,
                generated_at=datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
            )

            with _local_timezone("America/Los_Angeles"):
                rendered = render_full_observer_dashboard(
                    snapshot,
                    theme="day",
                    color=False,
                    width=88,
                    height=24,
                )

            self.assertIn("LOGBOOK WATCH", rendered)
            self.assertIn("view day", rendered)
            self.assertIn("LATEST FINISHED JOB  2026-05-15T04:12:00-07:00", rendered)
            self.assertIn("CONTROLS", rendered)
            self.assertIn("q quit", rendered)
            self.assertIn("RECENT FINISHED", rendered)
            self.assertIn("FAILURES AND REVIEW", rendered)
            self.assertIn("STATS", rendered)
            self.assertNotIn(str(config.processing_root), rendered)
            self.assertNotIn(str(config.recorder.mount_path), rendered)
            self.assertTrue(all(len(line) <= 88 for line in rendered.splitlines()))

    def test_watch_full_ui_uses_terminal_dashboard(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            _seed_observer_fixture(config)

            plain = io.StringIO()
            with redirect_stdout(plain):
                code = main(
                    [
                        "watch",
                        "--env",
                        str(env_path),
                        "--once",
                        "--ui",
                        "full",
                        "--theme",
                        "night",
                        "--no-color",
                    ]
                )

            output = plain.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("LOGBOOK WATCH", output)
            self.assertIn("view night", output)
            self.assertIn("CONTROLS", output)
            self.assertIn("RECENT FINISHED", output)

    def test_curses_frame_is_compact_modern_and_path_safe(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            _seed_observer_fixture(config)
            reporter = SQLitePipelineReporter.start(
                config.sqlite_path,
                command="process-mounted-recorder",
                host="mimir",
                pid=123,
                now=lambda: datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
            )
            reporter.start_stage(
                "route",
                progress_total=5,
                now=lambda: datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
            )
            reporter.advance_stage(
                "route",
                progress_current=2,
                progress_total=5,
                now=lambda: datetime(2026, 5, 15, 12, 2, tzinfo=timezone.utc),
            )
            snapshot = build_observer_snapshot(
                config,
                generated_at=datetime(2026, 5, 15, 12, 3, tzinfo=timezone.utc),
            )

            with _local_timezone("America/Los_Angeles"):
                frame = render_curses_frame(
                    snapshot,
                    width=92,
                    height=22,
                    theme="day",
                    status_filter="all",
                    refresh_interval=1.5,
                )
            rendered = frame.text()

            self.assertEqual(frame.theme, "day")
            self.assertIn("Logbook Watch", rendered)
            self.assertIn("Latest finished job  2026-05-15T04:12:00-07:00", rendered)
            self.assertIn("Health  api ok  sqlite ok", rendered)
            self.assertIn("Run  process-mounted-recorder", rendered)
            self.assertIn("Stage  route  elapsed 03:00", rendered)
            self.assertIn("40% measured", rendered)
            self.assertIn("Recent finished", rendered)
            self.assertIn("Failures and review", rendered)
            self.assertIn("[q] quit", rendered)
            self.assertIn("┌", rendered)
            self.assertIn("├", rendered)
            self.assertIn("└", rendered)
            self.assertIn("│ Health", rendered)
            self.assertNotIn("+---", rendered)
            self.assertNotIn("|---", rendered)
            self.assertNotIn(str(config.processing_root), rendered)
            self.assertTrue(all(len(line) <= 92 for line in rendered.splitlines()))

    def test_curses_frame_shows_readable_dates_for_finished_jobs_and_failures(self) -> None:
        snapshot = observer_snapshot_from_dict(
            {
                **_empty_snapshot_payload(),
                "latest_finished_at": "2026-06-05T18:37:42+00:00",
                "recent_finished": [
                    {
                        "job_id": 114,
                        "status": "vault_synced",
                        "classification": "log",
                        "recorded_at": "2026-05-28T15:00:00+00:00",
                        "finished_at": "2026-06-05T18:37:42+00:00",
                        "duration_seconds": 704261,
                        "vault_synced": True,
                    },
                    {
                        "job_id": 116,
                        "status": "vault_synced",
                        "classification": "log",
                        "recorded_at": None,
                        "finished_at": "",
                        "duration_seconds": None,
                        "vault_synced": True,
                    }
                ],
                "recent_failures": [
                    {
                        "job_id": 115,
                        "status": "failed_transcription",
                        "classification": "meeting",
                        "occurred_at": "2026-06-05T18:39:10+00:00",
                        "safe_detail": "failed_transcription",
                    },
                    {
                        "job_id": 117,
                        "status": "failed_route",
                        "classification": "log",
                        "occurred_at": "2026-06-05Tbad-value",
                        "safe_detail": "failed_route",
                    }
                ],
            }
        )

        with _local_timezone("America/Los_Angeles"):
            frame = render_curses_frame(snapshot, width=100, height=22)
        rendered = frame.text()

        self.assertIn("ok   2026-06-05 11:37  #114   vault_synced", rendered)
        self.assertIn("fail 2026-06-05 11:39  #115   failed_transcription", rendered)
        self.assertIn("ok   ---- -- -- --:--  #116   vault_synced", rendered)
        self.assertIn("fail 2026-06-05 bad-v  #117   failed_route", rendered)
        self.assertNotIn("195:37:41", rendered)

    def test_timestamp_formatter_preserves_invalid_iso_fallbacks(self) -> None:
        self.assertEqual(
            format_observer_timestamp("2026-06-05Tbad-value"),
            "2026-06-05Tbad-value",
        )
        self.assertEqual(
            format_observer_timestamp("2026-06-05Tbad-value", style="short"),
            "2026-06-05 bad-v",
        )

    def test_snapshot_payload_preserves_empty_and_invalid_timestamp_fallbacks(self) -> None:
        snapshot = observer_snapshot_from_dict(
            {
                **_empty_snapshot_payload(),
                "current_run": {
                    "started_at": "",
                    "heartbeat_at": "2026-06-05Tbad-value",
                    "history": [{"ended_at": "2026-06-05T18:00:00+00:00"}],
                },
            }
        )

        with _local_timezone("America/Los_Angeles"):
            payload = snapshot.to_dict()

        self.assertEqual(payload["current_run"]["started_at"], "")
        self.assertEqual(payload["current_run"]["heartbeat_at"], "2026-06-05Tbad-value")
        self.assertEqual(
            payload["current_run"]["history"][0]["ended_at"],
            "2026-06-05T11:00:00-07:00",
        )

    def test_curses_frame_shows_mounted_recorder_and_eject_menu_when_idle(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            _seed_observer_fixture(config)
            snapshot = build_observer_snapshot(
                config,
                generated_at=datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
            )

            frame = render_curses_frame(
                snapshot,
                width=92,
                height=22,
                recorder_status=CursesRecorderStatus(
                    mounted=True,
                    volume_name="IC RECORDER",
                    writable=True,
                    eject_available=True,
                ),
            )
            rendered = frame.text()

            self.assertIn("Recorder  mounted  IC RECORDER  writable yes", rendered)
            self.assertIn("[e] eject", rendered)

    def test_curses_frame_hides_eject_when_pipeline_is_active(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            SQLitePipelineReporter.start(
                config.sqlite_path,
                command="process-mounted-recorder",
                host="mimir",
                pid=123,
                now=lambda: datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
            )
            snapshot = build_observer_snapshot(
                config,
                generated_at=datetime(2026, 5, 15, 12, 1, tzinfo=timezone.utc),
            )

            frame = render_curses_frame(
                snapshot,
                width=92,
                height=18,
                recorder_status=CursesRecorderStatus(
                    mounted=True,
                    volume_name="IC RECORDER",
                    writable=True,
                    eject_available=False,
                    blocked_reason="pipeline running",
                ),
            )
            rendered = frame.text()

            self.assertIn("Recorder  mounted  IC RECORDER  writable yes  eject blocked: pipeline running", rendered)
            self.assertNotIn("[e] eject", rendered)

    def test_curses_frame_shows_not_mounted_and_recorder_message(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            snapshot = build_observer_snapshot(
                config,
                generated_at=datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
            )

            frame = render_curses_frame(
                snapshot,
                width=92,
                height=18,
                recorder_status=CursesRecorderStatus(
                    mounted=False,
                    volume_name="IC RECORDER",
                    message="eject unavailable",
                ),
            )
            rendered = frame.text()

            self.assertIn("Recorder  not mounted  expected IC RECORDER  eject unavailable", rendered)
            self.assertNotIn("[e] eject", rendered)

    def test_curses_eject_guard_requires_mount_and_idle_pipeline(self) -> None:
        idle_snapshot = observer_snapshot_from_dict(
            {
                "generated_at": "2026-05-15T12:00:00+00:00",
                "health": {"api": "ok", "sqlite": "ok", "odin": "ok", "memgraph": "ok"},
                "current_run": None,
                "active_stage": None,
                "recent_finished": [],
                "recent_failures": [],
                "stats": {
                    "window": "24h",
                    "jobs_seen": 0,
                    "succeeded": 0,
                    "failed": 0,
                    "dead_letters": 0,
                    "p50_duration_seconds": 0,
                    "p90_duration_seconds": 0,
                },
            }
        )
        active_snapshot = observer_snapshot_from_dict(
            {
                **idle_snapshot.to_dict(),
                "current_run": {
                    "run_id": "run-1",
                    "command": "process-mounted-recorder",
                    "host": "mimir",
                    "pid": 123,
                    "started_at": "2026-05-15T12:00:00+00:00",
                    "heartbeat_at": "2026-05-15T12:00:00+00:00",
                    "elapsed_seconds": 0,
                    "heartbeat_age_seconds": 0,
                    "stale": False,
                },
            }
        )

        self.assertTrue(
            _should_eject_recorder(
                idle_snapshot,
                CursesRecorderStatus(mounted=True, volume_name="IC RECORDER", eject_available=True),
            )
        )
        self.assertFalse(
            _should_eject_recorder(
                active_snapshot,
                CursesRecorderStatus(mounted=True, volume_name="IC RECORDER", eject_available=True),
            )
        )
        self.assertFalse(
            _should_eject_recorder(
                idle_snapshot,
                CursesRecorderStatus(mounted=False, volume_name="IC RECORDER", eject_available=False),
            )
        )

    def test_cli_curses_recorder_status_uses_mount_state_and_blocks_active_pipeline(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            config.recorder.recordings_dir.mkdir(parents=True, exist_ok=True)
            idle_snapshot = build_observer_snapshot(
                config,
                generated_at=datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
            )

            idle_status = _curses_recorder_status(config, idle_snapshot)

            self.assertIs(idle_status.mounted, True)
            self.assertEqual(idle_status.volume_name, "IC RECORDER")
            self.assertIs(idle_status.eject_available, True)
            self.assertIsNone(idle_status.blocked_reason)

            SQLitePipelineReporter.start(
                config.sqlite_path,
                command="process-mounted-recorder",
                host="mimir",
                pid=123,
                now=lambda: datetime(2026, 5, 15, 12, 1, tzinfo=timezone.utc),
            )
            active_snapshot = build_observer_snapshot(
                config,
                generated_at=datetime(2026, 5, 15, 12, 2, tzinfo=timezone.utc),
            )

            active_status = _curses_recorder_status(config, active_snapshot)

            self.assertIs(active_status.mounted, True)
            self.assertIs(active_status.eject_available, False)
            self.assertEqual(active_status.blocked_reason, "pipeline running")

    def test_cli_curses_recorder_status_reports_unmounted_recorder(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            missing_config = replace(
                config,
                recorder=replace(config.recorder, mount_path=root / "MISSING"),
            )
            snapshot = build_observer_snapshot(
                config,
                generated_at=datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
            )

            status = _curses_recorder_status(missing_config, snapshot)

            self.assertIs(status.mounted, False)
            self.assertEqual(status.volume_name, "IC RECORDER")
            self.assertIs(status.eject_available, False)
            self.assertIsNone(status.writable)

    def test_eject_recorder_reports_unmounted_and_uses_diskutil_output(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            missing_config = replace(
                config,
                recorder=replace(config.recorder, mount_path=root / "MISSING"),
            )

            self.assertEqual(_eject_recorder(missing_config), (False, "recorder not mounted"))

            with patch("logbook.cli.subprocess.run") as run:
                run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")

                ok, detail = _eject_recorder(config)

            self.assertIs(ok, True)
            self.assertEqual(detail, "IC RECORDER")
            run.assert_called_once()

    def test_curses_quit_key_is_explicitly_supported(self) -> None:
        self.assertTrue(_is_curses_quit_key("q"))
        self.assertTrue(_is_curses_quit_key("\x1b"))
        self.assertFalse(_is_curses_quit_key("r"))

    def test_curses_frame_handles_truncation_idle_stage_and_sparse_eta(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            _seed_observer_fixture(config)
            reporter = SQLitePipelineReporter.start(
                config.sqlite_path,
                command="process-mounted-recorder",
                host="mimir",
                pid=123,
                now=lambda: datetime(2026, 5, 15, 10, 0, tzinfo=timezone.utc),
            )
            snapshot_without_stage = build_observer_snapshot(
                config,
                generated_at=datetime(2026, 5, 15, 11, 2, tzinfo=timezone.utc),
            )
            no_stage = render_curses_frame(snapshot_without_stage, width=72, height=12)
            self.assertIn("Stage  none", no_stage.text())
            self.assertEqual(len(no_stage.lines), 12)

            reporter.start_stage(
                "diarize",
                input_bytes=42 * MB,
                now=lambda: datetime(2026, 5, 15, 11, 3, tzinfo=timezone.utc),
            )
            snapshot_sparse = build_observer_snapshot(
                config,
                generated_at=datetime(2026, 5, 15, 11, 7, tzinfo=timezone.utc),
            )
            sparse = render_curses_frame(snapshot_sparse, width=100, height=8)

            self.assertIn("collecting baseline", sparse.text())
            self.assertEqual(len(sparse.lines), 8)
            self.assertTrue(all(len(line) <= 100 for line in sparse.lines))

    def test_curses_frame_respects_small_terminal_dimensions_after_resize(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            _seed_observer_fixture(config)
            snapshot = build_observer_snapshot(
                config,
                generated_at=datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
            )

            frame = render_curses_frame(snapshot, width=40, height=8)

            self.assertEqual(len(frame.lines), 8)
            self.assertTrue(all(len(line) <= 40 for line in frame.lines))
            self.assertTrue(frame.lines[0].endswith("┐"))
            self.assertTrue(frame.lines[-1].endswith("┘"))

    def test_curses_frame_handles_tiny_terminal_heights(self) -> None:
        snapshot = observer_snapshot_from_dict(_empty_snapshot_payload())

        one_line = render_curses_frame(snapshot, width=12, height=1)
        two_lines = render_curses_frame(snapshot, width=12, height=2)

        self.assertEqual(one_line.lines, ("┌──────────┐",))
        self.assertEqual(two_lines.lines, ("┌──────────┐", "└──────────┘"))

    def test_curses_draw_frame_uses_full_terminal_width_for_right_border(self) -> None:
        class FakeScreen:
            def __init__(self) -> None:
                self.writes: list[tuple[int, int, str, int]] = []

            def erase(self) -> None:
                pass

            def getmaxyx(self) -> tuple[int, int]:
                return (3, 12)

            def addnstr(self, row: int, column: int, line: str, max_chars: int) -> None:
                self.writes.append((row, column, line[:max_chars], max_chars))

            def refresh(self) -> None:
                pass

        screen = FakeScreen()
        frame = render_curses_frame(observer_snapshot_from_dict(_empty_snapshot_payload()), width=12, height=3)

        _draw_frame(screen, frame)

        self.assertTrue(screen.writes)
        self.assertTrue(all(write[3] == 12 for write in screen.writes))
        self.assertTrue(all(write[2].endswith(("┐", "│", "┘")) for write in screen.writes))

    def test_curses_read_key_reports_terminal_resize(self) -> None:
        import curses

        class FakeScreen:
            def getch(self) -> int:
                return curses.KEY_RESIZE

        self.assertEqual(_read_key(FakeScreen(), 1.0), "resize")

    def test_watch_once_curses_ui_renders_frame(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            _seed_observer_fixture(config)

            plain = io.StringIO()
            with redirect_stdout(plain):
                code = main(
                    [
                        "watch",
                        "--env",
                        str(env_path),
                        "--once",
                        "--ui",
                        "curses",
                        "--theme",
                        "night",
                    ]
                )

            output = plain.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("Logbook Watch", output)
            self.assertIn("night", output)
            self.assertIn("Recent finished", output)

    def test_watch_once_curses_remote_api_stays_read_only_without_recorder_eject(self) -> None:
        payload = {
            "generated_at": "2026-05-15T12:00:00+00:00",
            "health": {"api": "ok", "sqlite": "ok", "odin": "ok", "memgraph": "ok"},
            "current_run": None,
            "active_stage": None,
            "recent_finished": [],
            "recent_failures": [],
            "stats": {
                "window": "24h",
                "jobs_seen": 0,
                "succeeded": 0,
                "failed": 0,
                "dead_letters": 0,
                "p50_duration_seconds": 0,
                "p90_duration_seconds": 0,
            },
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self) -> bytes:
                return json.dumps(payload).encode("utf-8")

        def fake_urlopen(request, timeout):
            return FakeResponse()

        plain = io.StringIO()
        with patch("logbook.cli.request.urlopen", side_effect=fake_urlopen):
            with redirect_stdout(plain):
                code = main(
                    [
                        "watch",
                        "--api",
                        "http://127.0.0.1:8788",
                        "--once",
                        "--ui",
                        "curses",
                    ]
                )

        output = plain.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("Recorder  unavailable in remote watch", output)
        self.assertNotIn("[e] eject", output)

    def test_watch_live_curses_requires_tty(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            _seed_observer_fixture(config)

            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = main(["watch", "--env", str(env_path), "--ui", "curses"])

            self.assertEqual(code, 2)
            self.assertIn("requires an interactive terminal", stderr.getvalue())

    def test_snapshot_can_probe_odin_and_memgraph_health(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            with env_path.open("a", encoding="utf-8") as env:
                env.write("\nMEMGRAPH_URI=bolt://memgraph.test:7687\n")
            config = load_app_config(env_path)
            _seed_observer_fixture(config)

            odin_client = SimpleNamespace(
                health=lambda: SimpleNamespace(healthy=True, detail="ready", payload={})
            )
            graph_client = SimpleNamespace(
                query=lambda cypher, parameters=None: [{"ok": 1}],
                close=lambda: None,
            )
            with (
                patch("logbook.observer.HttpOdinClient", return_value=odin_client) as odin,
                patch("logbook.observer.Neo4jMemgraphClient", return_value=graph_client) as graph,
            ):
                snapshot = build_observer_snapshot(config, probe_services=True)

            self.assertEqual(snapshot.health.odin, "ok")
            self.assertEqual(snapshot.health.memgraph, "ok")
            odin.assert_called_once()
            graph.assert_called_once()

    def test_snapshot_marks_unreachable_services_unavailable(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            with env_path.open("a", encoding="utf-8") as env:
                env.write("\nMEMGRAPH_URI=bolt://memgraph.test:7687\n")
            config = load_app_config(env_path)
            _seed_observer_fixture(config)

            odin_client = SimpleNamespace(
                health=lambda: SimpleNamespace(healthy=False, detail="not_ready", payload={})
            )
            with (
                patch("logbook.observer.HttpOdinClient", return_value=odin_client),
                patch("logbook.observer.Neo4jMemgraphClient", side_effect=RuntimeError("offline")),
            ):
                snapshot = build_observer_snapshot(config, probe_services=True)

            self.assertEqual(snapshot.health.odin, "unavailable")
            self.assertEqual(snapshot.health.memgraph, "unavailable")

    def test_watch_can_fetch_remote_api_snapshot_with_read_token(self) -> None:
        payload = {
            "generated_at": "2026-05-15T12:00:00+00:00",
            "health": {"api": "ok", "sqlite": "ok", "odin": "ok", "memgraph": "ok"},
            "current_run": None,
            "active_stage": None,
            "recent_finished": [],
            "recent_failures": [],
            "stats": {
                "window": "24h",
                "jobs_seen": 0,
                "succeeded": 0,
                "failed": 0,
                "dead_letters": 0,
                "p50_duration_seconds": 0,
                "p90_duration_seconds": 0,
            },
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self) -> bytes:
                return json.dumps(payload).encode("utf-8")

        requested = {}

        def fake_urlopen(request, timeout):
            requested["url"] = request.full_url
            requested["auth"] = request.headers.get("Authorization")
            requested["timeout"] = timeout
            return FakeResponse()

        plain = io.StringIO()
        with patch.dict(os.environ, {"LOGBOOK_READ_TOKEN": "read-secret"}):
            with patch("logbook.cli.request.urlopen", side_effect=fake_urlopen):
                with redirect_stdout(plain):
                    code = main(
                        [
                            "watch",
                            "--api",
                            "http://127.0.0.1:8788",
                            "--read-token-env",
                            "LOGBOOK_READ_TOKEN",
                            "--once",
                            "--json",
                        ]
                    )

        self.assertEqual(code, 0)
        self.assertEqual(requested["url"], "http://127.0.0.1:8788/observer/snapshot")
        self.assertEqual(requested["auth"], "Bearer read-secret")
        self.assertEqual(requested["timeout"], 10)
        self.assertEqual(json.loads(plain.getvalue())["health"]["odin"], "ok")

    def test_resolve_watch_theme_uses_day_night_boundaries(self) -> None:
        self.assertEqual(
            resolve_watch_theme("auto", now=datetime(2026, 5, 15, 12, 0)),
            "day",
        )
        self.assertEqual(
            resolve_watch_theme("auto", now=datetime(2026, 5, 15, 23, 0)),
            "night",
        )
        self.assertEqual(resolve_watch_theme("night"), "night")

    def test_snapshot_reports_active_run_and_stage_from_telemetry(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            reporter = SQLitePipelineReporter.start(
                config.sqlite_path,
                command="process-mounted-recorder",
                host="mimir",
                pid=123,
                now=lambda: datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
            )
            reporter.start_stage(
                "copy",
                job_id=42,
                input_bytes=1024,
                progress_total=10,
                safe_detail=str(config.processing_root / "inbox" / "secret.mp3"),
                now=lambda: datetime(2026, 5, 15, 12, 1, tzinfo=timezone.utc),
            )

            snapshot = build_observer_snapshot(
                config,
                generated_at=datetime(2026, 5, 15, 12, 3, tzinfo=timezone.utc),
            )
            rendered = render_observer_snapshot(snapshot)

            self.assertIsNotNone(snapshot.current_run)
            self.assertEqual(snapshot.current_run["command"], "process-mounted-recorder")
            self.assertEqual(snapshot.current_run["status"], "running")
            self.assertEqual(snapshot.current_run["elapsed_seconds"], 180)
            self.assertEqual(snapshot.current_run["heartbeat_age_seconds"], 120)
            self.assertFalse(snapshot.current_run["stale"])
            self.assertIsNotNone(snapshot.active_stage)
            self.assertEqual(snapshot.active_stage["stage"], "copy")
            self.assertEqual(snapshot.active_stage["job_id"], 42)
            self.assertEqual(snapshot.active_stage["progress_kind"], "unknown")
            self.assertEqual(snapshot.active_stage["elapsed_seconds"], 120)
            self.assertEqual(
                snapshot.active_stage["safe_detail"],
                "<processing_root>/inbox/secret.mp3",
            )
            self.assertNotIn(str(config.processing_root), json.dumps(snapshot.to_dict()))
            self.assertIn("Run process-mounted-recorder", rendered)
            self.assertIn("stage copy", rendered)
            self.assertIn("job 42", rendered)

    def test_snapshot_uses_fresh_heartbeat_for_long_running_stage(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            reporter = SQLitePipelineReporter.start(
                config.sqlite_path,
                command="process-mounted-recorder",
                host="mimir",
                pid=123,
                now=lambda: datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
            )
            reporter.start_stage(
                "transcribe",
                now=lambda: datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
            )
            reporter.heartbeat(now=lambda: datetime(2026, 5, 15, 12, 6, tzinfo=timezone.utc))

            snapshot = build_observer_snapshot(
                config,
                generated_at=datetime(2026, 5, 15, 12, 7, tzinfo=timezone.utc),
                stale_after_seconds=300,
            )

            self.assertIsNotNone(snapshot.current_run)
            self.assertFalse(snapshot.current_run["stale"])
            self.assertEqual(snapshot.current_run["heartbeat_age_seconds"], 60)
            self.assertIsNotNone(snapshot.active_stage)
            self.assertEqual(snapshot.active_stage["stage"], "transcribe")
            self.assertEqual(snapshot.active_stage["elapsed_seconds"], 420)

    def test_snapshot_reports_recent_pipeline_run_failures(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            old_reporter = SQLitePipelineReporter.start(
                config.sqlite_path,
                command="old-process-mounted-recorder",
                host="mimir",
                pid=124,
                now=lambda: datetime(2026, 5, 14, 10, 0, tzinfo=timezone.utc),
            )
            old_reporter.finish_run(
                status="failed",
                exit_code=2,
                now=lambda: datetime(2026, 5, 14, 10, 1, tzinfo=timezone.utc),
            )
            reporter = SQLitePipelineReporter.start(
                config.sqlite_path,
                command="process-mounted-recorder",
                host="mimir",
                pid=123,
                now=lambda: datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
            )
            reporter.start_stage(
                "transcribe",
                safe_detail=str(config.processing_root / "private.mp3"),
                now=lambda: datetime(2026, 5, 15, 12, 1, tzinfo=timezone.utc),
            )
            reporter.finish_stage(
                "transcribe",
                event="failed",
                safe_detail=str(config.processing_root / "private.mp3"),
                now=lambda: datetime(2026, 5, 15, 12, 2, tzinfo=timezone.utc),
            )
            reporter.finish_run(
                status="failed",
                exit_code=1,
                now=lambda: datetime(2026, 5, 15, 12, 2, tzinfo=timezone.utc),
            )

            snapshot = build_observer_snapshot(
                config,
                generated_at=datetime(2026, 5, 15, 12, 3, tzinfo=timezone.utc),
            )
            rendered = render_observer_snapshot(snapshot)

            self.assertEqual(snapshot.stats.failed, 1)
            self.assertEqual(snapshot.recent_failures[0].source, "pipeline_run")
            self.assertEqual(snapshot.recent_failures[0].command, "process-mounted-recorder")
            self.assertNotIn("old-process-mounted-recorder", rendered)
            self.assertIn("transcribe: <processing_root>/private.mp3", rendered)
            self.assertIn("fail  run", rendered)

    def test_snapshot_reports_inbox_written_jobs_as_recent_finished(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            _write_recording(config.recorder.recordings_dir / "260515_1130.mp3", 11, 30)
            candidate = next(
                item
                for item in discover_recordings(config.recorder.recordings_dir)
                if item.filename == "260515_1130.mp3"
            )
            ledger = open_ledger(config.sqlite_path, initialize=True)
            try:
                job = ledger.record_discovery(
                    candidate,
                    "checksum-inbox",
                    "IC RECORDER",
                    seen_at="2026-05-15T11:30:00+00:00",
                )
                inbox_written = ledger.mark_routed(
                    job.checksum_sha256,
                    "log",
                    Path("10 - Logs/00 - Inbox/2026/05-May/2026-05-15/later-log.md"),
                    "inbox_written",
                    routed_at="2026-05-15T11:45:00+00:00",
                )
            finally:
                ledger.close()

            snapshot = build_observer_snapshot(
                config,
                generated_at=datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(snapshot.latest_finished_at, "2026-05-15T11:45:00+00:00")
            self.assertIn(
                inbox_written.id,
                [item.job_id for item in snapshot.recent_finished],
            )

    def test_snapshot_reports_transcribed_and_diarized_jobs_as_recent_finished(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            _write_recording(config.recorder.recordings_dir / "260605_0830.mp3", 8, 30)
            _write_recording(config.recorder.recordings_dir / "260605_0845.mp3", 8, 45)
            candidates = {
                item.filename: item for item in discover_recordings(config.recorder.recordings_dir)
            }
            ledger = open_ledger(config.sqlite_path, initialize=True)
            try:
                transcribed = ledger.record_discovery(
                    candidates["260605_0830.mp3"],
                    "checksum-transcribed",
                    "IC RECORDER",
                    seen_at="2026-06-05T16:30:00+00:00",
                )
                ledger.mark_copied(
                    transcribed.checksum_sha256,
                    config.processing_root / "inbox" / transcribed.source_filename,
                    copied_at="2026-06-05T16:31:00+00:00",
                )
                transcribed = ledger.mark_transcribed(
                    transcribed.checksum_sha256,
                    "odin-transcribed",
                    config.processing_root / "transcripts" / "transcribed.json",
                    "fake-large-v3",
                    transcribed_at="2026-06-05T16:36:00+00:00",
                )
                diarized = ledger.record_discovery(
                    candidates["260605_0845.mp3"],
                    "checksum-diarized",
                    "IC RECORDER",
                    seen_at="2026-06-05T16:45:00+00:00",
                )
                ledger.mark_copied(
                    diarized.checksum_sha256,
                    config.processing_root / "inbox" / diarized.source_filename,
                    copied_at="2026-06-05T16:46:00+00:00",
                )
                ledger.mark_transcribed(
                    diarized.checksum_sha256,
                    "odin-diarized",
                    config.processing_root / "transcripts" / "diarized.json",
                    "fake-large-v3",
                    transcribed_at="2026-06-05T16:50:00+00:00",
                )
                diarized = ledger.mark_diarized(
                    diarized.checksum_sha256,
                    "odin-diarized",
                    config.processing_root / "diarization" / "diarized.json",
                    "fake-pyannote",
                    diarized_at="2026-06-05T16:51:00+00:00",
                )
            finally:
                ledger.close()

            snapshot = build_observer_snapshot(
                config,
                generated_at=datetime(2026, 6, 5, 19, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(snapshot.latest_finished_at, "2026-06-05T16:51:00+00:00")
            self.assertEqual(
                [item.job_id for item in snapshot.recent_finished],
                [diarized.id, transcribed.id],
            )

    def test_pipeline_failure_rows_preserve_failed_stage_job_id(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            reporter = SQLitePipelineReporter.start(
                config.sqlite_path,
                command="process-mounted-recorder",
                host="mimir",
                pid=123,
                now=lambda: datetime(2026, 6, 5, 18, 0, tzinfo=timezone.utc),
            )
            reporter.start_stage(
                "route",
                job_id=118,
                now=lambda: datetime(2026, 6, 5, 18, 1, tzinfo=timezone.utc),
            )
            reporter.finish_stage(
                "route",
                event="failed",
                job_id=118,
                safe_detail="exit_code=1",
                now=lambda: datetime(2026, 6, 5, 18, 2, tzinfo=timezone.utc),
            )
            reporter.finish_run(
                status="failed",
                exit_code=1,
                now=lambda: datetime(2026, 6, 5, 18, 2, tzinfo=timezone.utc),
            )

            snapshot = build_observer_snapshot(
                config,
                generated_at=datetime(2026, 6, 5, 19, 0, tzinfo=timezone.utc),
            )
            with _local_timezone("America/Los_Angeles"):
                frame = render_curses_frame(snapshot, width=120, height=24, theme="day")
            rendered = frame.text()

            self.assertEqual(snapshot.recent_failures[0].job_id, 118)
            self.assertIn("fail 2026-06-05 11:02  #118", rendered)
            self.assertNotIn("#0", rendered)

    def test_curses_pipeline_failure_without_job_id_renders_run_id_not_fake_job_zero(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            reporter = SQLitePipelineReporter.start(
                config.sqlite_path,
                command="process-mounted-recorder",
                host="mimir",
                pid=123,
                now=lambda: datetime(2026, 6, 5, 18, 0, tzinfo=timezone.utc),
            )
            reporter.start_stage(
                "route",
                now=lambda: datetime(2026, 6, 5, 18, 1, tzinfo=timezone.utc),
            )
            reporter.finish_stage(
                "route",
                event="failed",
                safe_detail="exit_code=1",
                now=lambda: datetime(2026, 6, 5, 18, 2, tzinfo=timezone.utc),
            )
            reporter.finish_run(
                status="failed",
                exit_code=1,
                now=lambda: datetime(2026, 6, 5, 18, 2, tzinfo=timezone.utc),
            )

            snapshot = build_observer_snapshot(
                config,
                generated_at=datetime(2026, 6, 5, 19, 0, tzinfo=timezone.utc),
            )
            with _local_timezone("America/Los_Angeles"):
                frame = render_curses_frame(snapshot, width=120, height=24, theme="day")
            rendered = frame.text()

            self.assertEqual(snapshot.recent_failures[0].job_id, 0)
            self.assertIn("fail 2026-06-05 11:02  run", rendered)
            self.assertNotIn("#0", rendered)

    def test_curses_failure_without_job_or_run_id_renders_unknown_subject(self) -> None:
        snapshot = observer_snapshot_from_dict(
            {
                **_empty_snapshot_payload(),
                "recent_failures": [
                    {
                        "job_id": 0,
                        "status": "failed",
                        "classification": None,
                        "occurred_at": "2026-06-05T18:02:00+00:00",
                        "safe_detail": "unknown failure",
                        "source": "job",
                    }
                ],
            }
        )

        with _local_timezone("America/Los_Angeles"):
            frame = render_curses_frame(snapshot, width=100, height=22, theme="day")
        rendered = frame.text()

        self.assertIn("fail 2026-06-05 11:02  #?", rendered)
        self.assertNotIn("#0", rendered)

    def test_snapshot_ignores_pipeline_failure_query_errors(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            _seed_observer_fixture(config)
            with patch("logbook.observer.sqlite3.connect", side_effect=sqlite3.Error("locked")):
                failures = _failed_pipeline_runs(
                    config.sqlite_path,
                    datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc),
                    config,
                )

            self.assertEqual(failures, ())

    def test_stage_duration_history_is_materialized_when_stage_succeeds(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            reporter = SQLitePipelineReporter.start(
                config.sqlite_path,
                command="process-mounted-recorder",
                host="mimir",
                pid=123,
                now=lambda: datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
            )
            reporter.start_stage(
                "transcribe",
                input_bytes=50 * MB,
                now=lambda: datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
            )
            reporter.finish_stage(
                "transcribe",
                event="succeeded",
                now=lambda: datetime(2026, 5, 15, 12, 10, tzinfo=timezone.utc),
            )

            ledger = open_ledger(config.sqlite_path)
            try:
                row = ledger.connection.execute(
                    """
                    SELECT stage, route_kind, model, input_size_bucket, sample_count,
                           duration_p50_seconds, duration_p90_seconds,
                           average_seconds_per_mb
                    FROM pipeline_stage_durations
                    """
                ).fetchone()
            finally:
                ledger.close()

            self.assertEqual(row["stage"], "transcribe")
            self.assertEqual(row["route_kind"], "unknown")
            self.assertEqual(row["model"], "unknown")
            self.assertEqual(row["input_size_bucket"], "10-100mb")
            self.assertEqual(row["sample_count"], 1)
            self.assertEqual(row["duration_p50_seconds"], 600)
            self.assertEqual(row["duration_p90_seconds"], 600)
            self.assertAlmostEqual(row["average_seconds_per_mb"], 12.0)

    def test_stage_duration_history_rebuilds_from_legacy_started_and_succeeded_events(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            ledger = open_ledger(config.sqlite_path, initialize=True)
            try:
                with ledger.connection:
                    ledger.connection.execute(
                        """
                        INSERT INTO pipeline_runs (
                            id, command, host, pid, started_at, heartbeat_at, status
                        )
                        VALUES (
                            'legacy-run', 'process-mounted-recorder', 'mimir', 123,
                            '2026-05-15T11:00:00+00:00',
                            '2026-05-15T11:02:00+00:00',
                            'succeeded'
                        )
                        """
                    )
                    ledger.connection.execute(
                        """
                        INSERT INTO pipeline_stage_events (
                            run_id, stage, event, occurred_at, progress_kind,
                            input_size_bucket
                        )
                        VALUES (
                            'legacy-run', 'route', 'started',
                            '2026-05-15T11:00:00+00:00',
                            'unknown', 'unknown'
                        )
                        """
                    )
                    ledger.connection.execute(
                        """
                        INSERT INTO pipeline_stage_events (
                            run_id, stage, event, occurred_at, progress_kind,
                            input_size_bucket
                        )
                        VALUES (
                            'legacy-run', 'route', 'succeeded',
                            '2026-05-15T11:02:00+00:00',
                            'unknown', 'unknown'
                        )
                        """
                    )
            finally:
                ledger.close()
            reporter = SQLitePipelineReporter.start(
                config.sqlite_path,
                command="process-mounted-recorder",
                host="mimir",
                pid=123,
                now=lambda: datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
            )
            ledger = open_ledger(config.sqlite_path)
            try:
                row = ledger.connection.execute(
                    """
                    SELECT sample_count, duration_p50_seconds, duration_p90_seconds
                    FROM pipeline_stage_durations
                    WHERE stage = 'route'
                      AND input_size_bucket = 'unknown'
                    """
                ).fetchone()
            finally:
                ledger.close()
            self.assertEqual(row["sample_count"], 1)
            self.assertEqual(row["duration_p50_seconds"], 120)
            self.assertEqual(row["duration_p90_seconds"], 120)

            reporter.start_stage("route", now=lambda: datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc))
            reporter.finish_stage(
                "route",
                event="succeeded",
                now=lambda: datetime(2026, 5, 15, 12, 4, tzinfo=timezone.utc),
            )

            ledger = open_ledger(config.sqlite_path)
            try:
                row = ledger.connection.execute(
                    """
                    SELECT sample_count, duration_p50_seconds, duration_p90_seconds
                    FROM pipeline_stage_durations
                    WHERE stage = 'route'
                      AND input_size_bucket = 'unknown'
                    """
                ).fetchone()
            finally:
                ledger.close()

            self.assertEqual(row["sample_count"], 2)
            self.assertEqual(row["duration_p50_seconds"], 120)
            self.assertEqual(row["duration_p90_seconds"], 240)

    def test_snapshot_estimates_active_stage_from_duration_history(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            _record_stage_duration(config.sqlite_path, "transcribe", 0, 300)
            _record_stage_duration(config.sqlite_path, "transcribe", 20, 600)
            _record_stage_duration(config.sqlite_path, "transcribe", 40, 900)
            active = SQLitePipelineReporter.start(
                config.sqlite_path,
                command="process-mounted-recorder",
                host="mimir",
                pid=123,
                now=lambda: datetime(2026, 5, 15, 13, 0, tzinfo=timezone.utc),
            )
            active.start_stage(
                "transcribe",
                input_bytes=42 * MB,
                now=lambda: datetime(2026, 5, 15, 13, 0, tzinfo=timezone.utc),
            )

            snapshot = build_observer_snapshot(
                config,
                generated_at=datetime(2026, 5, 15, 13, 5, tzinfo=timezone.utc),
            )
            rendered = render_observer_snapshot(snapshot)

            self.assertIsNotNone(snapshot.active_stage)
            self.assertEqual(snapshot.active_stage["progress_kind"], "estimated")
            self.assertEqual(snapshot.active_stage["progress_percent"], 50.0)
            self.assertEqual(snapshot.active_stage["eta_seconds"], 300)
            self.assertEqual(snapshot.active_stage["confidence"], "medium")
            self.assertEqual(snapshot.active_stage["sample_count"], 3)
            self.assertEqual(snapshot.active_stage["estimated_duration_seconds"], 600)
            self.assertEqual(snapshot.active_stage["p90_duration_seconds"], 900)
            self.assertIn("[##########..........] 50% estimated  ETA 05:00", rendered)
            self.assertIn("3 samples", rendered)
            self.assertTrue(all(len(line) <= 100 for line in rendered.splitlines()))

    def test_snapshot_prefers_measured_progress_over_duration_estimate(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            _record_stage_duration(config.sqlite_path, "route", 0, 300)
            _record_stage_duration(config.sqlite_path, "route", 20, 600)
            _record_stage_duration(config.sqlite_path, "route", 40, 900)
            reporter = SQLitePipelineReporter.start(
                config.sqlite_path,
                command="process-mounted-recorder",
                host="mimir",
                pid=123,
                now=lambda: datetime(2026, 5, 15, 13, 0, tzinfo=timezone.utc),
            )
            reporter.start_stage(
                "route",
                progress_total=5,
                now=lambda: datetime(2026, 5, 15, 13, 0, tzinfo=timezone.utc),
            )
            reporter.advance_stage(
                "route",
                progress_current=2,
                progress_total=5,
                now=lambda: datetime(2026, 5, 15, 13, 2, tzinfo=timezone.utc),
            )

            snapshot = build_observer_snapshot(
                config,
                generated_at=datetime(2026, 5, 15, 13, 3, tzinfo=timezone.utc),
            )
            rendered = render_observer_snapshot(snapshot)

            self.assertIsNotNone(snapshot.active_stage)
            self.assertEqual(snapshot.active_stage["stage"], "route")
            self.assertEqual(snapshot.active_stage["elapsed_seconds"], 180)
            self.assertEqual(snapshot.active_stage["progress_kind"], "measured")
            self.assertEqual(snapshot.active_stage["progress_percent"], 40.0)
            self.assertEqual(snapshot.active_stage["eta_status"], "measured")
            self.assertIsNone(snapshot.active_stage["eta_seconds"])
            self.assertIn("[########............] 40% measured", rendered)
            self.assertNotIn("ETA", rendered)

    def test_snapshot_collects_baseline_when_history_is_sparse(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            _record_stage_duration(config.sqlite_path, "diarize", 0, 480)
            active = SQLitePipelineReporter.start(
                config.sqlite_path,
                command="process-mounted-recorder",
                host="mimir",
                pid=123,
                now=lambda: datetime(2026, 5, 15, 13, 0, tzinfo=timezone.utc),
            )
            active.start_stage(
                "diarize",
                input_bytes=42 * MB,
                now=lambda: datetime(2026, 5, 15, 13, 0, tzinfo=timezone.utc),
            )

            snapshot = build_observer_snapshot(
                config,
                generated_at=datetime(2026, 5, 15, 13, 3, tzinfo=timezone.utc),
            )

            self.assertIsNotNone(snapshot.active_stage)
            self.assertEqual(snapshot.active_stage["progress_kind"], "unknown")
            self.assertIsNone(snapshot.active_stage["eta_seconds"])
            self.assertEqual(snapshot.active_stage["confidence"], "none")
            self.assertEqual(snapshot.active_stage["sample_count"], 1)
            self.assertEqual(snapshot.active_stage["eta_status"], "collecting_baseline")
            self.assertIn("collecting baseline", render_observer_snapshot(snapshot))

    def test_snapshot_marks_stale_run_when_heartbeat_is_old(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            config = load_app_config(env_path)
            reporter = SQLitePipelineReporter.start(
                config.sqlite_path,
                command="process-mounted-recorder",
                host="mimir",
                pid=123,
                now=lambda: datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
            )
            reporter.start_stage(
                "transcribe",
                now=lambda: datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
            )

            snapshot = build_observer_snapshot(
                config,
                generated_at=datetime(2026, 5, 15, 12, 7, tzinfo=timezone.utc),
                stale_after_seconds=300,
            )

            self.assertIsNotNone(snapshot.current_run)
            self.assertTrue(snapshot.current_run["stale"])
            self.assertEqual(snapshot.current_run["heartbeat_age_seconds"], 420)
            self.assertIn("stale", render_observer_snapshot(snapshot))


def _write_env(root: Path) -> Path:
    env_path = root / ".env"
    env_path.write_text(
        "\n".join(
            [
                f"LOGBOOK_PROCESSING_ROOT={root / 'VoiceIngest'}",
                "SONY_RECORDER_VOLUME_NAME=IC RECORDER",
                f"SONY_RECORDER_MOUNT_PATH={root / 'IC RECORDER'}",
                "SONY_RECORDER_RECORDINGS_PATH=/REC_FILE/FOLDER01",
                "ODIN_API_BASE_URL=http://odin.test",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "IC RECORDER" / "REC_FILE" / "FOLDER01").mkdir(parents=True)
    return env_path


@contextmanager
def _local_timezone(name: str):
    original = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()


def _seed_observer_fixture(config) -> dict[str, int]:
    recordings_dir = config.recorder.recordings_dir
    _write_recording(recordings_dir / "260515_0900.mp3", 9, 0)
    _write_recording(recordings_dir / "260515_1000.mp3", 10, 0)
    _write_recording(recordings_dir / "260515_1100.mp3", 11, 0)
    candidates = {
        candidate.filename: candidate
        for candidate in discover_recordings(recordings_dir)
    }
    transcript_dir = config.processing_root / "transcripts"
    transcript_dir.mkdir(parents=True)

    ledger = open_ledger(config.sqlite_path, initialize=True)
    try:
        log_job = ledger.record_discovery(
            candidates["260515_0900.mp3"],
            "checksum-log",
            "IC RECORDER",
            seen_at="2026-05-15T09:00:00+00:00",
        )
        ledger.mark_copied(
            log_job.checksum_sha256,
            config.processing_root / "inbox" / log_job.source_filename,
            copied_at="2026-05-15T09:05:00+00:00",
        )
        ledger.mark_transcribed(
            log_job.checksum_sha256,
            "odin-log",
            transcript_dir / "job-1.json",
            "fake-large-v3",
            transcribed_at="2026-05-15T09:20:00+00:00",
        )
        ledger.mark_routed(
            log_job.checksum_sha256,
            "log",
            Path("10 - Logs/00 - Inbox/2026/05-May/2026-05-15/log.md"),
            "inbox_written",
            routed_at="2026-05-15T09:25:00+00:00",
        )
        consolidated = ledger.mark_consolidated(
            log_job.checksum_sha256,
            Path("06 - Timestamps/2026/05-May/2026-05-15-Friday-Log.md"),
            consolidated_at="2026-05-15T09:30:00+00:00",
        )

        dead_job = ledger.record_discovery(
            candidates["260515_1000.mp3"],
            "checksum-dead",
            "IC RECORDER",
            seen_at="2026-05-15T10:00:00+00:00",
        )
        dead_letter = ledger.mark_routed(
            dead_job.checksum_sha256,
            "dead_letter",
            Path("99 - Dead Letters/2026/05-May/dead.md"),
            "dead_letter_written",
            routed_at="2026-05-15T10:45:00+00:00",
        )

        failed_job = ledger.record_discovery(
            candidates["260515_1100.mp3"],
            "checksum-failed",
            "IC RECORDER",
            seen_at="2026-05-15T11:00:00+00:00",
        )
        with ledger.connection:
            ledger.connection.execute(
                """
                UPDATE recording_jobs
                SET status = 'failed_transcription',
                    last_seen_at = '2026-05-15T11:12:00+00:00'
                WHERE checksum_sha256 = ?
                """,
                (failed_job.checksum_sha256,),
            )
    finally:
        ledger.close()

    return {
        "consolidated_id": consolidated.id,
        "dead_letter_id": dead_letter.id,
        "failed_id": failed_job.id,
    }


def _empty_snapshot_payload() -> dict[str, object]:
    return {
        "generated_at": "2026-05-15T12:00:00+00:00",
        "latest_finished_at": None,
        "health": {"api": "ok", "sqlite": "ok", "odin": "unknown", "memgraph": "not_configured"},
        "current_run": None,
        "active_stage": None,
        "recent_finished": [],
        "recent_failures": [],
        "stats": {
            "window": "24h",
            "jobs_seen": 0,
            "succeeded": 0,
            "failed": 0,
            "dead_letters": 0,
            "p50_duration_seconds": None,
            "p90_duration_seconds": None,
        },
    }


def _record_stage_duration(sqlite_path: Path, stage: str, minute_offset: int, duration_seconds: int) -> None:
    start = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc) + timedelta(minutes=minute_offset)
    reporter = SQLitePipelineReporter.start(
        sqlite_path,
        command="process-mounted-recorder",
        host="mimir",
        pid=123,
        now=lambda: start,
    )
    reporter.start_stage(
        stage,
        input_bytes=42 * MB,
        now=lambda: start,
    )
    reporter.finish_stage(
        stage,
        event="succeeded",
        now=lambda: start + timedelta(seconds=duration_seconds),
    )
    reporter.finish_run(
        status="succeeded",
        exit_code=0,
        now=lambda: start + timedelta(seconds=duration_seconds),
    )


def _write_recording(path: Path, hour: int, minute: int) -> None:
    path.write_bytes(f"fake mp3 bytes {path.name}".encode("utf-8"))
    timestamp = datetime(2026, 5, 15, hour, minute, 54).timestamp()
    os.utime(path, (timestamp, timestamp))
