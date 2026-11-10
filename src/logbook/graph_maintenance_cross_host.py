"""Experimental cross-host lock coordination; not enabled for production."""
from __future__ import annotations

import json
import re
import select
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from logbook.config import AppConfig
from logbook.graph_maintenance_launcher import StartupRejected, with_logbook_lock_descriptors, with_logbook_locks
from logbook.graph_maintenance_recovery import DockerRecovery
from logbook.processing_restart_guard import arm_guard, clear_guard


class SshMaintenance:
    """Call a pre-staged remote entrypoint using a fresh SSH connection each time."""

    def __init__(self, host: str, identity: Path, entrypoint: Path, request: dict):
        if not re.fullmatch(r"[A-Za-z0-9_.@-]+", host) or host.startswith("-"):
            raise ValueError("invalid SSH host")
        if not identity.is_absolute() or not entrypoint.is_absolute():
            raise ValueError("absolute paths required")
        self.host, self.identity, self.entrypoint, self.request = host, identity, entrypoint, request

    def _command(self, action: str) -> str:
        return shlex.join(['sudo', '-n', '/usr/bin/python3', str(self.entrypoint), action])

    def invoke(self, action: str, payload: dict | None = None) -> str:
        if action not in {"launch", "status", "preflight", "retry", "stage", "outcome", "offline", "authorize", "named-offline", "named-authorize", "named-status", "named-outcome", "named-check", "named-stage", "named-preflight", "named-launch", "named-cancel"}:
            raise ValueError("invalid remote action")
        command = self._command(action)
        try:
            result = subprocess.run(
                ["ssh", "-i", str(self.identity), "-o", "BatchMode=yes", "-o",
                 "ConnectTimeout=5", "-o", "StrictHostKeyChecking=yes", self.host, command],
                input=json.dumps(self.request if payload is None else payload), capture_output=True, text=True,
                timeout=240 if action in {"offline", "authorize", "named-offline", "named-authorize"} else 15,
            )
        except subprocess.TimeoutExpired as error:
            raise TimeoutError("remote maintenance request timed out") from error
        if result.returncode != 0:
            raise ConnectionError("remote maintenance request failed")
        return result.stdout.strip()

    def outcome(self) -> str:
        result = self.invoke("outcome")
        if result not in {"retained", "rolled-back", "not-retained"}:
            raise RuntimeError("repair outcome is not confirmed")
        return result

    def stage(self, plan: dict) -> None:
        if self.invoke("stage", {**self.request, "plan_data": plan}) != "STAGED":
            raise ConnectionError("remote staging rejected")

    def preflight(self) -> None:
        if self.invoke("preflight") != "PREFLIGHT_OK":
            raise ConnectionError("remote preflight rejected")

    def retry(self) -> None:
        if self.invoke("retry") != "RETRY_STARTED":
            raise ConnectionError("remote retry rejected")

    def launch(self) -> None:
        result = self.invoke("launch")
        if result == "REJECTED":
            raise StartupRejected("remote startup rejected before any work")
        if result != "LAUNCHED":
            raise ConnectionError("unexpected remote launch response")

    def recovered(self) -> bool:
        return self.invoke("status") == "RECOVERED"


class NamedGraphMaintenance(SshMaintenance):
    """Bounded named graph launch and recovery commands."""

    def _command(self, action: str) -> str:
        if action not in {'named-check','named-stage','named-preflight','named-launch','named-cancel','named-status','named-outcome','named-offline','named-authorize'}:
            raise ValueError('invalid named action')
        return shlex.join(['sudo','-n','/usr/local/libexec/logbook-repair-dispatcher',action])

    def stage(self, plan: dict) -> None:
        proof = getattr(self, 'plan_proof', None)
        if not isinstance(proof, dict):
            raise ValueError('producer plan proof required')
        if self.invoke("named-stage", {**self.request, "plan_data": plan, "plan_proof": proof}) != "STAGED":
            raise ConnectionError("remote staging rejected")

    def check(self, plan: dict) -> None:
        proof = getattr(self, 'plan_proof', None)
        if not isinstance(proof, dict):
            raise ValueError('producer plan proof required')
        if self.invoke('named-check', {**self.request, 'plan_data':plan, 'plan_proof':proof}) != 'CHECK_OK':
            raise ConnectionError('remote repair check rejected')

    def preflight(self) -> None:
        if self.invoke("named-preflight") != "PREFLIGHT_OK":
            raise ConnectionError("remote preflight rejected")

    def launch(self) -> None:
        result = self.invoke("named-launch")
        if result == "REJECTED":
            raise StartupRejected("remote startup rejected")
        if result != "LAUNCHED":
            raise ConnectionError("unexpected remote launch response")

    def cancel_unstarted(self) -> bool:
        result = self.invoke('named-cancel')
        if result not in {'CANCELLED', 'PENDING'}:
            raise ConnectionError('unexpected cancellation response')
        return result == 'CANCELLED'

    def recovered(self) -> bool:
        return self.invoke("named-status") == "RECOVERED"

    def outcome(self) -> str:
        result = self.invoke("named-outcome")
        if result not in {"retained", "rolled-back", "not-retained"}:
            raise RuntimeError("repair outcome is not confirmed")
        return result


def recovery_confirmed(operations: DockerRecovery, timer: str) -> bool:
    """Require a completed successful watchdog, stopped worker and no fence."""
    identity = operations.run(["docker", "inspect", "--format",
                               "{{.Id}} {{.State.Pid}} {{.State.Running}}",
                               operations.container_id]).strip()
    if identity != f"{operations.container_id} {operations.pid} true":
        return False
    service = timer + ".service"

    def state(unit: str, key: str) -> str:
        return operations.run(["systemctl", "show", unit, "--property=" + key, "--value"]).strip()

    start = state(service, "ExecMainStartTimestampMonotonic")
    if not start.isdigit() or int(start) == 0:
        return False
    if (state(service, "Result") != "success" or state(service, "ActiveState") != "active"
            or state(service, "SubState") != "exited"):
        return False
    if state(operations.unit, "ActiveState") not in {"inactive", "failed"}:
        return False
    result = operations.run(["nsenter", "-t", str(operations.pid), "-n", "nft", "-j",
                             "list", "tables"])
    entries = json.loads(result)["nftables"]
    if not any("metainfo" in entry for entry in entries):
        raise RuntimeError("incomplete firewall listing")
    return not any(entry.get("table", {}).get("family") == "inet"
                   and entry.get("table", {}).get("name") == operations.table
                   for entry in entries)


def coordinate_window(config: AppConfig, launch: Callable[[], None],
                      recovered: Callable[[], bool], *, timeout_seconds: float,
                      poll_seconds: float = 0.2) -> None:
    """Keep local locks while independently checking the remote recovery."""
    def operation() -> None:
        failure = None
        try:
            launch()
        except Exception as error:
            failure = error
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                confirmed = recovered()
            except Exception:
                confirmed = False
            if confirmed:
                if failure is not None:
                    raise failure
                return
            time.sleep(poll_seconds)
        raise RuntimeError("remote recovery was not confirmed") from failure

    with_logbook_locks(config, operation)


def coordinate_leased_window(config: AppConfig, remote: SshMaintenance, *,
                             timeout_seconds: float, stage_plan: dict | Callable[[], dict] | None = None) -> None:
    """Keep inherited locks in an independent process until remote recovery."""
    unit = remote.request.get("unit", "")
    if not isinstance(unit, str) or not re.fullmatch(
        r"logbook-graph-maintenance-[a-f0-9]{32}\.service", unit
    ):
        raise ValueError("fresh maintenance unit required")

    if stage_plan is None:
        remote.preflight()

    def operation(pipeline_fd: int, graph_fd: int) -> None:
        if remote.recovered():
            raise ValueError("maintenance unit already reports recovery")
        checked_plan = stage_plan() if callable(stage_plan) else stage_plan
        arm_guard(config.processing_root, unit)
        if checked_plan is not None:
            remote.stage(checked_plan)
            remote.preflight()
        lease = subprocess.Popen(
            [sys.executable, "-m", "logbook.graph_maintenance_lease", str(pipeline_fd), str(graph_fd)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, pass_fds=(pipeline_fd, graph_fd), start_new_session=True,
        )
        assert lease.stdin is not None and lease.stdout is not None
        try:
            lease.stdin.write(json.dumps({"host": remote.host, "identity": str(remote.identity),
                                          "entrypoint": str(remote.entrypoint),
                                          "request": remote.request, "processing_root": str(config.processing_root),
                                          "window": unit}) + "\n")
            lease.stdin.flush()
            ready, _, _ = select.select([lease.stdout], [], [], 5)
            if not ready or lease.stdout.readline().strip() != "READY":
                raise RuntimeError("independent lease did not start")
            # The holder must begin checking before launch can reach the remote host.
            lease.stdin.write("CHECK\n")
            lease.stdin.flush()
        except Exception:
            lease.terminate()
            lease.wait(timeout=5)
            if stage_plan is None:
                clear_guard(config.processing_root, unit)
            raise
        failure = None
        try:
            remote.launch()
        except StartupRejected:
            lease.terminate()
            lease.wait(timeout=5)
            if stage_plan is None:
                clear_guard(config.processing_root, unit)
            raise
        except Exception as error:
            failure = error
        lease.stdin.close()
        try:
            result = lease.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as error:
            raise TimeoutError(f"recovery unconfirmed; independent lease pid {lease.pid}") from error
        if result != 0:
            raise RuntimeError("independent lease stopped before confirmed recovery")
        if failure is not None:
            raise failure

    with_logbook_lock_descriptors(config, operation)
