# Codex Workspace Kit

This directory gives future Codex sessions fast, durable context without rereading every source from scratch.

Use it this way:

1. Start with `project-context.md` and `docs/PRD.md`.
2. Check `decisions.md` before changing architecture.
3. Pull the next work item from `backlog.md`, respecting dependency IDs.
4. Update `research.md` when external assumptions change.
5. Check `../lessons-learned.md` before touching live ingest, vault sync, launchd, or cleanup behavior.
6. Update `Changelog.md` for notable changes.
7. Add to `../lessons-learned.md` when a live incident exposes a recoverable failure mode or prevention rule.

The backlog dependency graph has also been mirrored into memgraph with project key `logbook` so dependency questions can be queried instead of manually re-parsed.
