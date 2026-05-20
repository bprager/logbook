# Agent Instructions

## Project Context

This repository defines a local-first voice recorder to Obsidian logbook system. Read `docs/PRD.md` first for product requirements, then use the working context in `.codex/` for current planning, decisions, and backlog.

Important project files:

- `.codex/project-context.md` - durable context for future Codex sessions.
- `.codex/research.md` - research notes and source links.
- `.codex/backlog.md` - implementation backlog with dependencies.
- `.codex/decisions.md` - accepted decisions and open questions.
- `Changelog.md` - notable changes, following Keep a Changelog.

## Operating Rules

- Use the memgraph MCP server for dependency and graph queries unless the user says otherwise.
- Store and sync personal preferences learned from the user via the memgraph MCP server.
- OpenClaw runtime owner on this host is `clawdbot`.
- Do not run OpenClaw gateway or node services under `bernd`.
- Do not delete recordings from the Sony recorder during initial ingest.
- Delete local and Sony-recorder source audio only after the one-week retention gate confirms processing, Markdown generation, and vault sync completed.
- Make every processing and cleanup step recoverable and auditable.
- Prefer deterministic routing by spoken prefix before any semantic or fuzzy classification.
- Update `Changelog.md` under `[Unreleased]` for notable repo, behavior, or workflow changes.

## Engineering Guardrails

- Keep the system local-first: transient audio, SQLite ledger, generated Markdown, and OpenClaw status should all work without cloud services except explicitly configured model downloads, vault GitHub sync, or tokens.
- Treat the SQLite ledger as the source of truth for idempotency, job state, late arrivals, and log rebuilds.
- Render final daily logs atomically from ledger/inbox state; never append opportunistically to canonical log files.
- Enforce the invariant: one date has one canonical final daily log path.
- Keep OpenClaw integration narrow and bounded: status, reprocess, rescue, and rebuild requests only.
- Build tests around routing, path generation, idempotency, late arrivals, and atomic rewrite behavior before expanding convenience features.
