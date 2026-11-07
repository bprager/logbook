"""Experimental remote entrypoint for a pre-staged maintenance fixture."""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logbook.fixture_boot_guard import FixtureBootGuard, verify_fixture_binding  # noqa: E402
from logbook.fixture_offline_recovery import record_fixture_storage, recover_offline  # noqa: E402
from logbook.fixture_recovery_release import authorize_fixture_restart, fixture_restarted, verify_offline_receipt  # noqa: E402
from logbook.graph_maintenance_cross_host import recovery_confirmed  # noqa: E402
from logbook.graph_maintenance_launcher import DockerLaunch, StartupRejected  # noqa: E402
from logbook.graph_maintenance_recovery import DockerRecovery  # noqa: E402
from logbook.graph_maintenance_stage import stage_plan  # noqa: E402
from logbook.graph_maintenance_worker import completion_matches, completion_value, read_private_json  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"launch", "status", "preflight", "retry", "stage", "outcome", "offline", "authorize"}:
        raise ValueError("expected launch or status")
    request = json.load(sys.stdin)
    recovery = DockerRecovery(request["container_id"], request["pid"],
                              request["port"], request["unit"], Path(request["private_dir"]) / "stopped.json")
    launcher = DockerLaunch(recovery, Path(request["private_dir"]), Path(request["worker"]),
                            Path(request["recovery_file"]), request["seconds"],
                            Path(request["recovery_worker"]) if request.get("recovery_worker") else None)
    if request.get('sandbox_only') and sys.argv[1] in {'stage', 'retry'}:
        name = recovery.run(['docker', 'inspect', '--format', '{{.Name}}', request['container_id']]).strip()
        if not name.startswith('/logbook-fixture-'):
            raise ValueError('fixture container required')
    if sys.argv[1] == "authorize":
        if request.get('sandbox_only') is not True:
            raise ValueError('fixture recovery required')
        authorize_fixture_restart(request, recovery)
        print('STARTED')
    elif sys.argv[1] == "offline":
        if request.get('sandbox_only') is not True:
            raise ValueError('fixture recovery required')
        print(recover_offline(request, recovery))
    elif sys.argv[1] == "outcome":
        try:
            normal = recovery_confirmed(recovery, launcher.timer)
        except Exception:
            normal = False
        if not normal:
            try:
                if request.get('sandbox_only') is True and fixture_restarted(request, recovery):
                    print(verify_offline_receipt(request)['outcome'])
                    return
            except Exception:
                pass
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
        if request.get('sandbox_only'):
            verify_fixture_binding(recovery, request['boot_guard_service'])
            FixtureBootGuard(Path(request['boot_guard_dir']), request['boot_guard_service'], recovery.run).allow()
        print(outcome)
    elif sys.argv[1] == "stage":
        launcher.verify_target()
        if request.get('sandbox_only'):
            verify_fixture_binding(recovery, request['boot_guard_service'])
            record_fixture_storage(request, recovery)
            FixtureBootGuard(Path(request['boot_guard_dir']), request['boot_guard_service'], recovery.run).disarm()
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
        try:
            confirmed = recovery_confirmed(recovery, launcher.timer)
        except Exception:
            confirmed = False
        if not confirmed and request.get('sandbox_only') is True:
            try:
                confirmed = fixture_restarted(request, recovery)
            except Exception:
                confirmed = False
        print("RECOVERED" if confirmed else "PENDING")


if __name__ == "__main__":  # pragma: no cover
    main()
