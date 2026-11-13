"""Experimental remote entrypoint for a pre-staged maintenance fixture."""
from __future__ import annotations

import json
import os
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logbook.fixture_boot_guard import FixtureBootGuard, MemgraphBootGuard, verify_fixture_binding, verify_memgraph_binding  # noqa: E402
from logbook.fixture_offline_recovery import record_fixture_storage, record_named_graph_storage, recover_offline, recover_named_graph_offline, named_offline_required  # noqa: E402
from logbook.fixture_recovery_release import (authorize_fixture_restart, fixture_restarted, verify_offline_receipt,
                                              authorize_named_graph_restart, verify_named_graph_receipt, _validate_named)  # noqa: E402
from logbook.graph_maintenance_cross_host import recovery_confirmed  # noqa: E402
from logbook.graph_maintenance_launcher import DockerLaunch, StartupRejected  # noqa: E402
from logbook.graph_maintenance_recovery import DockerRecovery  # noqa: E402
from logbook.graph_maintenance_stage import stage_plan  # noqa: E402
from logbook.named_graph_approval import verify_bundle_marker  # noqa: E402
from logbook.standing_graph_policy import authorize_repair  # noqa: E402
from logbook.plan_attestation import verify_plan  # noqa: E402
from logbook.graph_maintenance_worker import completion_matches, completion_value, read_private_json, verify_prestart  # noqa: E402


def verify_execution_marker(request: dict) -> Path:
    """Verify release identity while retaining the code of the fixed executor."""
    executor = Path(__file__).resolve().parent.parent
    guard = Path(request['boot_guard_dir'])
    if executor == guard/'executor':
        match = re.fullmatch(r'logbook-graph-maintenance-([a-f0-9]{32})\.service', request['unit'])
        if match is None:
            raise ValueError('invalid protected unit')
        release = guard/'bundles'/('bundle-'+match.group(1))
        verify_bundle_marker(request, executor, release)
    else:
        verify_bundle_marker(request, executor)
    return executor


def prepare_claimed_cancellation(request: dict, recovery: DockerRecovery, launcher: DockerLaunch) -> None:
    """Reopen the named boot guard only when the exact target has no fence."""
    launcher.verify_target()
    listing = json.loads(recovery.run(['nsenter', '-t', str(recovery.pid), '-n',
                                       'nft', '-j', 'list', 'tables']))
    entries = listing['nftables']
    if not isinstance(entries, list) or not any(isinstance(entry, dict) and 'metainfo' in entry for entry in entries):
        raise RuntimeError('incomplete firewall listing')
    if any(isinstance(entry, dict) and entry.get('table', {}).get('family') == 'inet'
           and entry.get('table', {}).get('name') == recovery.table for entry in entries):
        raise RuntimeError('maintenance fence remains')
    verify_memgraph_binding(recovery)
    MemgraphBootGuard(Path(request['boot_guard_dir']), recovery.run).allow()


def confirmed_normal_outcome(request: dict) -> str:
    journal = Path(request['journal'])
    if request.get('profile') == 'named-graph' and not os.path.lexists(journal):
        verify_prestart(request, Path(request['private_dir']))
        result = read_private_json(journal.with_name(journal.name + '.outcome'))
        if result != {**completion_value(request, None), 'outcome': 'not-retained'}:
            raise ValueError('unconfirmed pre-start outcome')
        return 'not-retained'
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
    return outcome


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"launch", "status", "preflight", "retry", "stage", "outcome", "offline", "authorize", "named-offline", "named-authorize", "named-status", "named-outcome", "named-check", "named-stage", "named-preflight", "named-launch", "named-cancel"}:
        raise ValueError("expected launch or status")
    request = json.load(sys.stdin)
    recovery = DockerRecovery(request["container_id"], request["pid"],
                              request["port"], request["unit"], Path(request["private_dir"]) / "stopped.json")
    launcher = DockerLaunch(recovery, Path(request["private_dir"]), Path(request["worker"]),
                            Path(request["recovery_file"]), request["seconds"],
                            Path(request["recovery_worker"]) if request.get("recovery_worker") else None)
    if sys.argv[1].startswith('named-'):
        if request.get('profile') != 'named-graph' or request.get('sandbox_only') is True:
            raise ValueError('named graph recovery state required')
        _validate_named(request)
        action = sys.argv[1]
        if action in {'named-check', 'named-stage'}:
            launcher.verify_target()
            bundle = verify_execution_marker(request)
            authorize_repair(request, request['plan_data'], Path(request['boot_guard_dir']))
            verify_plan(request['plan_data'], request['job_id'], request.get('plan_proof'),
                        Path(request['boot_guard_dir'])/'plan.allowed_signers')
            recovery.run(['/usr/bin/nsenter', '-t', str(request['pid']), '-n', '/usr/bin/env',
                          f'PYTHONPATH={bundle}', '/usr/bin/python3', '-m',
                          'logbook.repair_schema_preflight', str(request['port'])],
                         json.dumps(request['plan_data']), timeout=30)
            if action == 'named-check':
                print('CHECK_OK')
                return
            record_named_graph_storage(request, recovery)
            stage_plan(request, bundle)
            print('STAGED')
        elif action in {'named-preflight', 'named-launch'}:
            verify_execution_marker(request)
            plan = read_private_json(Path(request['private_dir']) / 'plan.json')
            authorize_repair(request, plan, Path(request['boot_guard_dir']))
            proof = read_private_json(Path(request['private_dir']) / 'plan-proof.json')
            verify_plan(plan, request['job_id'], proof, Path(request['boot_guard_dir'])/'plan.allowed_signers')
            if action == 'named-preflight':
                launcher.preflight()
                print('PREFLIGHT_OK')
            else:
                launcher.after_schedule = lambda: MemgraphBootGuard(Path(request['boot_guard_dir']), recovery.run).disarm()
                try:
                    launcher.run_window()
                except StartupRejected:
                    print('REJECTED')
                else:
                    print('LAUNCHED')
        elif action == 'named-cancel':
            def prepare() -> None:
                prepare_claimed_cancellation(request, recovery, launcher)
            print('CANCELLED' if launcher.cancel_unstarted(prepare) else 'PENDING')
        elif action in {'named-status', 'named-outcome'} and launcher.cancelled() is True:
            print('RECOVERED' if action == 'named-status' else 'not-retained')
        elif action == 'named-offline':
            try:
                outcome = verify_named_graph_receipt(request)['outcome']
            except (FileNotFoundError,ValueError):
                outcome = recover_named_graph_offline(request,recovery)
            print(outcome)
        elif action == 'named-authorize':
            authorize_named_graph_restart(request, recovery)
            print('STARTED')
        else:
            try:
                normal = recovery_confirmed(recovery, launcher.timer)
            except Exception:
                normal = False
            if normal:
                if action == 'named-status':
                    print('RECOVERED')
                else:
                    outcome = confirmed_normal_outcome(request)
                    verify_memgraph_binding(recovery)
                    MemgraphBootGuard(Path(request['boot_guard_dir']), recovery.run).allow()
                    print(outcome)
            else:
                try:
                    confirmed = fixture_restarted(request, recovery, _named=True)
                except Exception:
                    confirmed = False
                if action == 'named-status':
                    if confirmed:
                        print('RECOVERED')
                    else:
                        try:
                            offline = named_offline_required(request,recovery)
                        except (OSError,ValueError):
                            offline = False
                        print('OFFLINE_REQUIRED' if offline else 'PENDING')
                else:
                    print(verify_named_graph_receipt(request)['outcome'] if confirmed else 'PENDING')
        return
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
        outcome = confirmed_normal_outcome(request)
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
