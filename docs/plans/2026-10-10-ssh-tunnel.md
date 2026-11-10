# Automatic SSH transport

## Approved direction

Use the operator's existing `jump-mimir` SSH alias through fenrir. A separate
per-user LaunchAgent owns one foreground SSH tunnel, starts at login and retries
on exit with a 30-second throttle. The recorder agent independently keeps its
mount/login/minute schedule and durable retries. No VPN, new public listener,
production activation, source deletion or credential replacement is implied.

The destination is always mimir's loopback receiver; the Mac listener must also
be explicit IPv4 loopback. SSH configuration supplies jump routing and keys.
Host-key verification is mandatory; password prompts, agent/X11 forwarding and
connection multiplexing are disabled for this dedicated process. Dead peers
are detected with 15-second keepalives and three missed replies. Failed local
binds exit; launchd retries without taking over another process's listener.

## Implementation checklist

- [x] Add `ssh_host` and `ssh_remote_port` agent settings, disabled by default.
  Reject option-like aliases, invalid ports, VPN mixing and non-loopback URLs.
  Cover valid and invalid combinations in `tests/test_remote_config.py`.
- [x] Generate `local.logbook.remote-tunnel.plist` in
  `src/logbook/remote_launchd.py`; add standalone `package-tunnel` CLI so an
  existing signed app need not be rebuilt. Include it in `package-agent` when
  SSH is configured. Test arguments, lifecycle, disabled configuration and CLI.
- [x] Verify OpenSSH accepts the generated options; exercise launchd restart
  with an isolated temporary test job and synthetic process, never production.
  Re-run agent offline/restart recovery tests and the full quality gate.
- [x] Document explicit installation/removal, password-free post-login preflight,
  separate pilot buffer/port, retry timing and remaining physical acceptance.
  Update architecture, decisions, backlog and changelog; commit and push.

## Acceptance boundary

The package generator never loads a service. Physical logout/reboot, travel
network changes, signed-app delivery and second-Mac checks remain operator tests.
Pilot receipts must stay in a separate Mac buffer and central ledger. Existing
Mac source snapshots remain unacknowledged by production until production receipt.
