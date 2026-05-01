# Dead Letter Management

Use the repo-local wrapper:

```bash
scripts/manage-dead-letters --env .env --action list
```

Assign a pending dead letter to the log route:

```bash
scripts/manage-dead-letters --env .env --action assign --job-id 123 --target log --reason "spoken log prefix was clipped"
```

That command is a dry run by default. To execute:

```bash
scripts/manage-dead-letters --env .env --action assign --job-id 123 --target log --reason "spoken log prefix was clipped" --execute
```

When executed, Logbook:

- writes the rescued log inbox note,
- records `dead_letter.assign` in `action_audit`,
- resets stale vault-sync state for the rescued job,
- rebuilds the canonical daily log for that recording date,
- reruns the daily-log entity linker.

Discard a pending dead letter:

```bash
scripts/manage-dead-letters --env .env --action discard --job-id 123 --reason "not a useful log entry" --execute
```

Discarding records `dead_letter.discard` in `action_audit` and removes the job from the pending dead-letter list. It does not delete source audio, recorder audio, or the generated dead-letter note.
