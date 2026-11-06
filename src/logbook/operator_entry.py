"""Fixture-only operator entry point for the experimental maintenance path."""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

from logbook.config import AppConfig, load_app_config
from logbook.graph_maintenance_cross_host import SshMaintenance
from logbook.graph_maintenance_recovery import DockerRecovery
from logbook.graph_maintenance_worker import private_directory, read_private_json
from logbook.operator_bundle import build_bundle, provision_bundle
from logbook.operator_window import stage_plan_and_run


def build_installed_bundle() -> bytes:
    import neo4j
    import pytz
    return build_bundle({'logbook':Path(__file__).parent, 'neo4j':Path(neo4j.__file__).parent,
                         'pytz':Path(pytz.__file__).parent})


def run_fixture(config: AppConfig, job_id: int, options: dict, state: Path) -> str:
    private_directory(state.parent)
    token = uuid.uuid4().hex
    parent = Path(options['remote_parent'])
    bundle, staging = parent/f'bundle-{token}', parent/f'window-{token}'
    journal = Path(options['journal_dir'])/f'{token}.json'
    unit = 'logbook-graph-maintenance-'+token+'.service'
    DockerRecovery(options['container_id'], options['pid'], options['port'], unit)
    request = {'container_id':options['container_id'], 'pid':options['pid'], 'port':options['port'],
               'unit':unit, 'private_dir':str(staging), 'journal':str(journal),
               'worker':str(staging/'worker'), 'recovery_file':str(bundle/'logbook/graph_maintenance_recovery.py'),
               'recovery_worker':str(staging/'restore'), 'seconds':options['seconds'], 'sandbox_only':True}
    entrypoint = bundle/'logbook/graph_maintenance_remote.py'
    saved = {'host':options['host'], 'identity':options['identity'], 'entrypoint':str(entrypoint), 'request':request}
    fd = os.open(state, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as output:
        json.dump(saved, output, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    directory = os.open(state.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    provision_bundle(options['host'], Path(options['identity']), bundle, staging, build_installed_bundle())
    remote = SshMaintenance(options['host'], Path(options['identity']), entrypoint, request)
    return stage_plan_and_run(config, job_id, remote, timeout_seconds=options['seconds']+270)


if __name__ == '__main__':  # pragma: no cover
    if len(sys.argv) != 5:
        raise SystemExit('usage: python -m logbook.operator_entry ENV JOB_ID PRIVATE_OPTIONS PRIVATE_STATE')
    print(run_fixture(load_app_config(Path(sys.argv[1])), int(sys.argv[2]),
                      read_private_json(Path(sys.argv[3])), Path(sys.argv[4])))
