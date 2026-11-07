"""Experimental composition of plan staging and an existing protected window."""
from pathlib import Path
from tempfile import TemporaryDirectory

from logbook.graph_maintenance_worker import read_private_json
from logbook.graph_maintenance_cross_host import SshMaintenance, coordinate_leased_window
from logbook.config import AppConfig
from logbook.operator_plan import prepare_job_plan


def stage_plan_and_run(config: AppConfig, job_id: int, remote: SshMaintenance, *, timeout_seconds: float) -> str:
    with TemporaryDirectory() as temporary:
        plan = Path(temporary) / 'plan.json'
        prepare_job_plan(config, job_id, plan)
        remote.stage(read_private_json(plan))
    coordinate_leased_window(config, remote, timeout_seconds=timeout_seconds)
    return remote.outcome()
