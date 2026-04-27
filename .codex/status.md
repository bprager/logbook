# Status

Updated: 2026-04-27

## Current Focus

First minor planning release completed.

## Active Request

- Update `README.md` into a polished GitHub intro.
- Prepare a minor version upgrade.
- Stage, commit, and push the repo.
- Keep `.env` local and excluded from the remote repo.

## Progress

- [x] Created `.codex/status.md`.
- [x] Reviewed `prager.ws` repository structure.
- [x] Identified relevant hosts, networking, service deployment, and secrets patterns.
- [x] Assessed communication options for Mac Mini, `odin`, Obsidian vault, and OpenClaw.
- [x] Proposed architecture and implementation sequence.
- [x] Updated README for GitHub.
- [x] Prepared `0.1.0` release notes.
- [x] Committed and pushed `0.1.0`.

## Notes

- OpenClaw runtime owner on this host remains `clawdbot`; do not run OpenClaw services under `bernd`.
- Use memgraph MCP for dependency and graph queries.
- `prager.ws` documents `fenrir` as edge/jump host, `odin` as main internal/observability host, `saga` as preferred storage, `qnap` as legacy storage, and `mimir` as OpenClaw/control-plane host.
- `odin` already runs the observability stack; Prometheus/Loki are internal-only, Grafana is bound on `192.168.1.3:3000` and reverse-proxied by `fenrir`.
- `mimir` is the right OpenClaw host and OpenClaw should use scoped APIs/credentials, not broad shell access.
- The PRD's Mac Mini is confirmed to be `mimir`.
- Recommended communication model: `mimir` keeps local SQLite/processing/vault writes, submits async jobs to `odin` over direct internal HTTP, OpenClaw reads/requests through a loopback status/action API, Prometheus scrapes health/metrics, and `saga` is backup/archive only.
- MVP auth decision: scoped bearer tokens in `.env`; separate tokens for `odin` jobs, Logbook read access, and Logbook action access.
- Obsidian vault: `https://github.com/bprager/obs-vault.git`; use Obsidian CLI to access and update it.
- Audio retention: do not link audio in Obsidian; delete local and Sony-recorder source audio after 24 hours once processing and vault sync are confirmed.
- Local `.env` placeholder created and ignored by git for tokens, Obsidian CLI settings, `odin` API config, Sony recorder mount details, and retention settings.
- Detailed plan captured in `.codex/architecture-plan.md`.
- `0.1.0` is the first minor planning release and is tagged as `v0.1.0`.
