# Bounded recovery watchdog retries

The actual recovery command can fail while draining transactions, leaving the
fence and source guard intact. A later retry through the installed guarded method
has been shown to succeed. The automatic watchdog currently performs only one
attempt, so this safe retry still requires a private owner login.

Reuse systemd supervision of the same recovery command: `Restart=on-failure`,
`RestartSec=5s`, `StartLimitIntervalSec=infinity`, `StartLimitBurst=3`. The infinite
interval bounds total starts, even when an attempt lasts a long time; a finite
rate window would eventually allow additional starts. A successful retained
oneshot service does not restart. `Type=oneshot` makes the existing
`TimeoutStartSec=240s` cover recovery execution. Manual guarded retry submits the
same properties with `--no-block`, so the caller does not wait on a service-start
job while its own short transport deadline runs.

This restarts recovery, never the repair worker. Every attempt retains the same
setup lock, exact target, saved stop proof, trusted recovery file, transaction
drain and rollback checks. Failure after the bounded attempts still leaves the
fence/guard in place. No new gateway verb, role, policy schema, write authority,
source-record limit or permanent supervisory service is introduced.

Primary references: systemd's own
[service documentation](https://raw.githubusercontent.com/systemd/systemd/main/man/systemd.service.xml)
and [unit documentation](https://raw.githubusercontent.com/systemd/systemd/main/man/systemd.unit.xml).
The documentation confirms failure restart, successful retained-service behavior
and the infinite interval for a total start bound. Actual manager compatibility
and bounded execution still require runtime verification; documentation alone
is not production proof.

Only `graph_maintenance_launcher.py` changes in the release package. Mimir's
existing source holder imports the unchanged lock helper in that module; watchdog
scheduling runs in the protected Odin executor. Verify that call-path distinction
and the exact one-file package delta before staging the owner update. Production
execution is not changed by this branch or by passing local tests.
