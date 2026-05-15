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

Rescue a pending dead letter as a meeting:

```bash
scripts/manage-dead-letters --env .env --action rescue --job-id 123 --target meeting --reason "actually a meeting"
```

That command is a dry run by default. It reports the planned meeting note path and the dead-letter
note that would be removed. To execute:

```bash
scripts/manage-dead-letters --env .env --action rescue --job-id 123 --target meeting --reason "actually a meeting" --execute
```

When executed, Logbook:

- keeps source audio untouched,
- diarizes the recording as a meeting even when the spoken meeting prefix was missed,
- routes the diarized transcript into `30 - Meetings`,
- records `dead_letter.rescue` in `action_audit`,
- clears stale vault-sync state for the rescued job,
- removes the obsolete generated dead-letter Markdown only after the meeting note is written.

Discard a pending dead letter:

```bash
scripts/manage-dead-letters --env .env --action discard --job-id 123 --reason "not a useful log entry" --execute
```

Discarding records `dead_letter.discard` in `action_audit` and removes the job from the pending dead-letter list. It does not delete source audio, recorder audio, or the generated dead-letter note.
