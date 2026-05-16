from __future__ import annotations

import html
import re
from importlib import resources
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from logbook.config import AppConfig
from logbook.observer import build_observer_snapshot


WEB_UI_VERSION = "1.1.0"


def create_watch_web_app(config: AppConfig, *, static_root: Path | None = None) -> FastAPI:
    root = static_root or watch_static_root()
    app = FastAPI(
        title="Logbook Watch",
        version=WEB_UI_VERSION,
        summary="Modern read-only web observer for the Logbook pipeline.",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/observer/snapshot")
    def observer_snapshot() -> dict[str, object]:
        return build_observer_snapshot(config, probe_services=True).to_dict()

    @app.get("/favicon.ico")
    def favicon() -> Response:
        return Response(status_code=204)

    if _has_built_ui(root):
        @app.get("/", response_class=HTMLResponse)
        def index() -> HTMLResponse:
            snapshot = build_observer_snapshot(config, probe_services=True).to_dict()
            return HTMLResponse(_inject_snapshot_fallback(root / "index.html", snapshot))

        app.mount("/", StaticFiles(directory=root, html=True), name="watch-static")
    else:

        @app.get("/{path:path}", response_class=HTMLResponse)
        def missing_ui(path: str) -> HTMLResponse:
            return HTMLResponse(_missing_ui_html(root, path), status_code=503)

    return app


def watch_static_root() -> Path:
    return Path(str(resources.files("logbook") / "static" / "watch"))


def _has_built_ui(root: Path) -> bool:
    return (root / "index.html").exists()


def _inject_snapshot_fallback(index_path: Path, snapshot: dict[str, object]) -> str:
    index_html = index_path.read_text(encoding="utf-8")
    fallback = _snapshot_fallback_html(snapshot)
    replaced = re.sub(
        r'<div id="root">.*?</div>',
        f'<div id="root">{fallback}</div>',
        index_html,
        count=1,
        flags=re.DOTALL,
    )
    return replaced if replaced != index_html else index_html


def _snapshot_fallback_html(snapshot: dict[str, object]) -> str:
    health = _as_dict(snapshot.get("health"))
    stats = _as_dict(snapshot.get("stats"))
    current_run = _as_dict(snapshot.get("current_run"))
    active_stage = _as_dict(snapshot.get("active_stage"))
    recent_finished = snapshot.get("recent_finished")
    recent_items = recent_finished if isinstance(recent_finished, list) else []
    status = "running" if current_run else "idle"
    stage = str(active_stage.get("stage") or "none") if active_stage else "none"
    progress = _format_percent(active_stage.get("progress_percent") if active_stage else None)
    recent_rows = "\n".join(
        _finished_fallback_row(item)
        for item in recent_items[:5]
        if isinstance(item, dict)
    )
    if not recent_rows:
        recent_rows = '<li class="watch-fallback-empty">No finished jobs in the window</li>'
    return f"""
      <main class="watch-fallback watch-fallback-dashboard">
        <section>
          <div class="watch-fallback-topline">
            <div>
              <h1>Logbook Watch</h1>
              <p>{_escape(snapshot.get("generated_at") or "waiting for snapshot")}</p>
            </div>
            <span>{_escape(status)}</span>
          </div>
          <p class="watch-fallback-muted">
            JavaScript has not started in this browser tab, so this is the server-rendered
            snapshot. Enable JavaScript for the live shadcn UI.
          </p>
          <div class="watch-fallback-grid">
            {_chip("API", health.get("api"))}
            {_chip("SQLite", health.get("sqlite"))}
            {_chip("Odin", health.get("odin"))}
            {_chip("Graph", health.get("memgraph"))}
          </div>
          <div class="watch-fallback-grid">
            {_metric("Stage", stage)}
            {_metric("Progress", progress)}
            {_metric("Jobs", stats.get("jobs_seen"))}
            {_metric("Success", stats.get("succeeded"))}
            {_metric("Failed", stats.get("failed"))}
            {_metric("Dead letters", stats.get("dead_letters"))}
          </div>
          <h2>Recent finished</h2>
          <ul class="watch-fallback-list">
            {recent_rows}
          </ul>
        </section>
      </main>
"""


def _finished_fallback_row(item: dict[str, object]) -> str:
    job_id = _escape(item.get("job_id") or "-")
    status = _escape(item.get("status") or "unknown")
    classification = _escape(item.get("classification") or "-")
    duration = _format_duration(item.get("duration_seconds"))
    return (
        '<li><span class="watch-fallback-job">#'
        f"{job_id}</span><span>{status}</span><span>{classification}</span><span>{duration}</span></li>"
    )


def _chip(label: str, value: object) -> str:
    return (
        '<div class="watch-fallback-chip"><span>'
        f"{_escape(label)}</span><strong>{_escape(value or 'unknown')}</strong></div>"
    )


def _metric(label: str, value: object) -> str:
    return (
        '<div class="watch-fallback-metric"><span>'
        f"{_escape(label)}</span><strong>{_escape(value if value is not None else '-')}</strong></div>"
    )


def _format_percent(value: object) -> str:
    return f"{float(value):.0f}%" if isinstance(value, (int, float)) else "0%"


def _format_duration(value: object) -> str:
    if not isinstance(value, int):
        return "00:00"
    minutes, seconds = divmod(max(0, value), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _missing_ui_html(root: Path, path: str) -> str:
    escaped_root = str(root)
    escaped_path = path.replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Logbook Watch UI Missing</title>
    <style>
      body {{ font-family: system-ui, sans-serif; margin: 2rem; max-width: 42rem; }}
      code {{ background: #f4f4f5; padding: .125rem .25rem; border-radius: .25rem; }}
    </style>
  </head>
  <body>
    <h1>Logbook Watch UI is not built</h1>
    <p>Requested <code>/{escaped_path}</code>, but no built watcher assets exist at
    <code>{escaped_root}</code>.</p>
    <p>Build them with <code>npm --prefix web/observer run build</code>.</p>
  </body>
</html>"""
