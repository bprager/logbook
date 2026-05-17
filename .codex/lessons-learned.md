# Codex Lessons Learned

This document captures project-specific lessons for future Codex sessions:
what worked, what was efficient, what caused friction, and what should be
checked earlier next time. It complements the root `lessons-learned.md`, which
is reserved for live operational incidents, recoveries, and prevention rules.
This is repo-local durable context only; it does not write to Memgraph,
OpenClaw, or any external memory system.

## Maintenance Rules

- Add a dated entry after substantial implementation, debugging, release, or
  collaboration work teaches something reusable.
- Focus on transferable guidance: efficient checks, pitfalls, good workflows,
  test strategies, and user preferences.
- Link to concrete files, commands, commits, screenshots, or observed symptoms
  when available.
- Keep secrets, bearer tokens, source audio paths inside notes, and private
  transcript content out of this file.
- Update this file alongside `.codex/status.md`, `.codex/backlog.md`,
  `Changelog.md`, and root `lessons-learned.md` when the lesson affects both
  agent workflow and live operations.

## 2026-05-17: Keep Agent Workflow Lessons Separate From Operational Incidents

**Learned:** The project now has two distinct kinds of durable learning:
operator-facing production lessons and Codex-facing collaboration/process
lessons. Mixing them would make both documents less useful.

**What worked:** Keeping root `lessons-learned.md` focused on live incidents
made release notes and runbooks easier to scan. Adding this `.codex` file gives
future agent sessions a place to record efficient workflows, debugging patterns,
and implementation habits without turning the operational incident log into a
general diary.

**Guidance:** Use root `lessons-learned.md` for production behavior that an
operator might need during recovery. Use `.codex/lessons-learned.md` for how
Codex should work better in this repo.

## 2026-05-16: Browser Logs Beat Guesswork For Watcher UI Failures

**Learned:** When the web watcher appeared stuck on the loading card even
though `/observer/snapshot` returned healthy JSON, server access logs showed
the browser was not requesting the JavaScript asset at all. That was the key
signal.

**What worked:** Comparing three facts narrowed the fault quickly:

- `GET /observer/snapshot` returned `200 OK` with valid JSON.
- The page requested CSS and favicon but not the JavaScript bundle.
- A server-rendered snapshot fallback made the page useful even when JavaScript
  was blocked, cached oddly, or not started.

**What did not work:** Repeatedly refreshing the same browser tab without
checking asset requests did not distinguish frontend runtime failure from
browser-side JavaScript blocking.

**Guidance:** For local web UI debugging, inspect network evidence first:
HTML, CSS, JavaScript asset, API endpoint, and browser console. A healthy API
with no JavaScript request usually means the browser or cache is the problem,
not the snapshot endpoint.

## 2026-05-16: Server-Rendered Fallbacks Are Cheap Insurance For Local Tools

**Learned:** A local operator UI should not strand the user on an empty loading
state when the backend already has a useful status snapshot.

**What worked:** Injecting a compact server-rendered observer snapshot into the
packaged `index.html` preserved the React app path while making the first paint
useful without JavaScript. Focused tests around empty recent jobs and long
duration formatting kept the fallback covered without broad refactoring.

**Guidance:** For read-only dashboards, prefer a useful static or
server-rendered initial state over a pure loading placeholder. The dynamic UI
can hydrate over it, but the fallback should still answer the operator's first
question.

## 2026-05-16: Changed-Line Coverage Ratchets Need Targeted Edge Tests

**Learned:** The quality gate enforces changed-line coverage, not global
coverage. Small fallback branches can fail the gate even when the feature works
manually.

**What worked:** Reading the missing line report and adding narrow tests for
the uncovered branches restored 100% changed-line coverage quickly. The useful
cases were no recent jobs, non-numeric duration, and hour-scale duration.

**What did not work:** Treating the gate as a formality after manual checks
cost an extra loop. It is faster to run the focused test and then the full
`scripts/quality-gate` before committing.

**Guidance:** After changing Python, expect to add tests for every new branch.
Use the diff-cover output as a to-do list, not as an annoyance.

## 2026-05-16: Terminal Controls Need Visible Affordances

**Learned:** Supporting a key in code is not enough. Operators need to see the
available controls in the terminal UI itself.

**What worked:** The curses watcher already accepted `q` and Escape, but
changing the footer to explicit keycaps like `[q] quit` made the behavior
discoverable. A tiny pure helper for quit-key handling made the interaction
easy to test without needing an interactive curses session.

**Guidance:** For terminal dashboards, put essential controls in a stable
footer and cover key semantics with pure tests. Keep live curses behavior thin
and manually verifiable.

## 2026-05-15: Release Prep Is Smoother When Version Touchpoints Are Searched

**Learned:** Minor releases touch more than Python package metadata. The web UI
package, API metadata, README status, release docs, changelog, and durable
Codex context all need to agree.

**What worked:** Running a repo-wide version search before editing exposed the
required update set: `VERSION`, `pyproject.toml`, `src/logbook/__init__.py`,
`src/logbook/api.py`, `src/logbook/watch_web.py`, `web/observer/package*.json`,
tests, README, changelog, release notes, and `.codex` context.

**Guidance:** Before a release commit, run:

```bash
rg -n "1\\.2\\.0|1\\.1\\.0|version|__version__" \
  VERSION pyproject.toml src tests README.md Changelog.md docs .codex web/observer/package*.json
```

Then run `scripts/quality-gate`, commit through the hook, and check
`git status --short --branch` before pushing.
