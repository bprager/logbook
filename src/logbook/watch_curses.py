from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from logbook.observer import (
    ObserverFailure,
    ObserverJobOutcome,
    ObserverSnapshot,
    filter_observer_snapshot,
    resolve_watch_theme,
)


SnapshotProvider = Callable[[], ObserverSnapshot]


@dataclass(frozen=True)
class CursesFrame:
    lines: tuple[str, ...]
    theme: str

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def render_curses_frame(
    snapshot: ObserverSnapshot,
    *,
    width: int = 100,
    height: int = 28,
    theme: str = "auto",
    status_filter: str = "all",
    refresh_interval: float = 2.0,
    now: datetime | None = None,
) -> CursesFrame:
    resolved_theme = resolve_watch_theme(theme, now=now)
    visible = filter_observer_snapshot(snapshot, status_filter)
    width = max(64, width)
    height = max(12, height)

    body_width = width - 2
    lines = [
        _rule(width, "top"),
        _line(
            (
                f"Logbook Watch  {visible.generated_at}  {resolved_theme}  "
                f"filter {status_filter}  refresh {refresh_interval:g}s"
            ),
            body_width,
        ),
        _line(_health_line(visible), body_width),
        _rule(width, "sep"),
    ]
    lines.extend(_run_lines(visible, body_width))
    lines.append(_rule(width, "sep"))
    lines.append(_line(_stats_line(visible), body_width))
    lines.append(_rule(width, "sep"))
    lines.extend(_section("Recent finished", _finished_lines(visible.recent_finished), body_width))
    lines.append(_rule(width, "sep"))
    lines.extend(_section("Failures and review", _failure_lines(visible.recent_failures), body_width))
    lines.append(_rule(width, "sep"))
    lines.append(_line("q quit  r refresh  a all  f failures  s success  d dead letters  +/- speed", body_width))
    lines.append(_rule(width, "bottom"))

    if len(lines) > height:
        footer = lines[-2:]
        lines = lines[: max(0, height - len(footer))] + footer
    if len(lines) < height:
        lines = lines[:-1] + [_line("", body_width)] * (height - len(lines)) + [lines[-1]]
    return CursesFrame(lines=tuple(_trim(line, width) for line in lines), theme=resolved_theme)


def run_curses_watch(
    snapshot_provider: SnapshotProvider,
    *,
    refresh_interval: float,
    theme: str,
    status_filter: str,
    fail_on: Callable[[ObserverSnapshot], int],
) -> int:  # pragma: no cover - exercised through pure renderer and manual terminal use
    import curses

    def _main(screen) -> int:
        curses.curs_set(0)
        screen.nodelay(True)
        active_filter = status_filter
        interval = refresh_interval
        last_code = 0
        while True:
            snapshot = snapshot_provider()
            last_code = fail_on(filter_observer_snapshot(snapshot, active_filter))
            height, width = screen.getmaxyx()
            frame = render_curses_frame(
                snapshot,
                width=width,
                height=height,
                theme=theme,
                status_filter=active_filter,
                refresh_interval=interval,
            )
            _draw_frame(screen, frame)
            key = _read_key(screen, interval)
            if key in {"q", "\x1b"}:
                return last_code
            if key == "f":
                active_filter = "failed"
            elif key == "a":
                active_filter = "all"
            elif key == "s":
                active_filter = "success"
            elif key == "d":
                active_filter = "dead_letter"
            elif key == "+":
                interval = max(0.25, interval / 2)
            elif key == "-":
                interval = min(60.0, interval * 2)

    return curses.wrapper(_main)


def _draw_frame(screen, frame: CursesFrame) -> None:  # pragma: no cover
    screen.erase()
    for row, line in enumerate(frame.lines):
        screen.addnstr(row, 0, line, max(0, screen.getmaxyx()[1] - 1))
    screen.refresh()


def _read_key(screen, refresh_interval: float) -> str | None:  # pragma: no cover
    deadline = time.monotonic() + max(0.0, refresh_interval)
    while time.monotonic() < deadline:
        try:
            value = screen.getch()
        except KeyboardInterrupt:
            return "q"
        if value != -1:
            return chr(value).lower()
        time.sleep(0.05)
    return None


def _health_line(snapshot: ObserverSnapshot) -> str:
    health = snapshot.health
    return f"Health  api {health.api}  sqlite {health.sqlite}  odin {health.odin}  graph {health.memgraph}"


def _run_lines(snapshot: ObserverSnapshot, width: int) -> list[str]:
    if snapshot.current_run is None:
        return [_line("Run  idle", width), _line("Stage  none", width)]

    run = snapshot.current_run
    stale = "  STALE" if run.get("stale") else ""
    lines = [
        _line(
            (
                f"Run  {run.get('command', '-')}  elapsed "
                f"{_format_duration(run.get('elapsed_seconds'))}  heartbeat "
                f"{run.get('heartbeat_age_seconds', '-')}s{stale}"
            ),
            width,
        )
    ]
    stage = snapshot.active_stage
    if stage is None:
        lines.append(_line("Stage  none", width))
        return lines

    job = f"  job {stage['job_id']}" if stage.get("job_id") is not None else ""
    lines.append(
        _line(
            f"Stage  {stage.get('stage', '-')}{job}  elapsed {_format_duration(stage.get('elapsed_seconds'))}",
            width,
        )
    )
    lines.append(_line(_progress_line(stage, width=max(10, width - 34)), width))
    return lines


def _progress_line(stage: dict[str, object], *, width: int) -> str:
    percent = _coerce_percent(stage.get("progress_percent"))
    kind = str(stage.get("progress_kind") or "unknown")
    eta = stage.get("eta_seconds")
    suffix = f"  ETA {_format_duration(eta)}" if isinstance(eta, int) else ""
    if stage.get("eta_status") == "collecting_baseline":
        suffix = f"  collecting baseline ({stage.get('sample_count', 0)} samples)"
    return f"{_progress_bar(percent, width)} {percent:3.0f}% {kind}{suffix}"


def _stats_line(snapshot: ObserverSnapshot) -> str:
    stats = snapshot.stats
    return (
        f"Stats {stats.window}  jobs {stats.jobs_seen}  success {stats.succeeded}  "
        f"failed {stats.failed}  dead letters {stats.dead_letters}  "
        f"p50 {_format_duration(stats.p50_duration_seconds)}  p90 {_format_duration(stats.p90_duration_seconds)}"
    )


def _section(title: str, rows: list[str], width: int) -> list[str]:
    lines = [_line(title, width)]
    lines.extend(_line(row, width) for row in (rows or ["none in window"])[:5])
    return lines


def _finished_lines(items: tuple[ObserverJobOutcome, ...]) -> list[str]:
    return [
        (
            f"ok   #{item.job_id:<5} {item.status:<18} "
            f"{item.classification or '-':<12} {_format_duration(item.duration_seconds)}"
        )
        for item in items
    ]


def _failure_lines(items: tuple[ObserverFailure, ...]) -> list[str]:
    return [
        (
            f"fail #{item.job_id:<5} {item.status:<18} "
            f"{item.classification or '-':<12} {item.safe_detail}"
        )
        for item in items
    ]


def _progress_bar(percent: float, width: int) -> str:
    width = max(8, min(width, 44))
    filled = min(width, max(0, round(width * percent / 100)))
    return "[" + ("#" * filled) + ("." * (width - filled)) + "]"


def _coerce_percent(value: object) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(100.0, float(value)))
    return 0.0


def _format_duration(value: object) -> str:
    if not isinstance(value, int):
        return "--:--"
    minutes, seconds = divmod(max(0, value), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _rule(width: int, kind: str) -> str:
    if kind == "top":
        return "+" + "-" * (width - 2) + "+"
    if kind == "bottom":
        return "+" + "-" * (width - 2) + "+"
    return "+" + "=" * (width - 2) + "+"


def _line(text: str, width: int) -> str:
    return "| " + _trim(text, max(0, width - 2)).ljust(max(0, width - 2)) + " |"


def _trim(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(0, width - 3)] + "..."
