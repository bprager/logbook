"""Experimental remote entrypoint for a pre-staged maintenance fixture."""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logbook.graph_maintenance_cross_host import recovery_confirmed  # noqa: E402
from logbook.graph_maintenance_launcher import DockerLaunch, StartupRejected  # noqa: E402
from logbook.graph_maintenance_recovery import DockerRecovery  # noqa: E402
from logbook.graph_maintenance_stage import stage_plan  # noqa: E402
from logbook.graph_maintenance_worker import completion_matches, completion_value, read_private_json  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"launch", "status", "preflight", "retry", "stage", "outcome"}:
        raise ValueError("expected launch or status")
    request = json.load(sys.stdin)
    recovery = DockerRecovery(request["container_id"], request["pid"],
                              request["port"], request["unit"], Path(request["private_dir"]) / "stopped.json")
    launcher = DockerLaunch(recovery, Path(request["private_dir"]), Path(request["worker"]),
                            Path(request["recovery_file"]), request["seconds"],
                            Path(request["recovery_worker"]) if request.get("recovery_worker") else None)
    if sys.argv[1] == "outcome":
        if not recovery_confirmed(recovery, launcher.timer):
            print("PENDING")
            return
        journal = Path(request['journal'])
        record = read_private_json(journal)
        batch = record['batch']
        if record.get('schema') != 1 or str(uuid.UUID(batch)) != batch:
            raise ValueError("invalid recovery journal")
        result = read_private_json(journal.with_name(journal.name + '.outcome'))
        outcome = result.get('outcome')
        if result != {**completion_value(request, batch), 'outcome': outcome} or outcome not in {'retained', 'rolled-back', 'not-retained'}:
            raise ValueError("unconfirmed repair outcome")
        completion = journal.with_name(journal.name + '.complete')
        if outcome == 'retained':
            if not completion_matches(journal, request, batch):
                raise ValueError("unconfirmed completion")
        elif os.path.lexists(completion):
            raise ValueError("unexpected completion receipt")
        print(outcome)
    elif sys.argv[1] == "stage":
        launcher.verify_target()
        stage_plan(request, Path(__file__).resolve().parent.parent)
        print("STAGED")
    elif sys.argv[1] == "retry":
        launcher.retry_recovery()
        print("RETRY_STARTED")
    elif sys.argv[1] == "preflight":
        launcher.preflight()
        print("PREFLIGHT_OK")
    elif sys.argv[1] == "launch":
        try:
            launcher.run_window()
        except StartupRejected:
            print("REJECTED")
        else:
            print("LAUNCHED")
    else:
        print("RECOVERED" if recovery_confirmed(recovery, launcher.timer) else "PENDING")


if __name__ == "__main__":  # pragma: no cover
    main()
