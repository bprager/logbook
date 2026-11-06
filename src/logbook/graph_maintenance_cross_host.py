"""Experimental cross-host lock coordination; not enabled for production."""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Callable

from logbook.config import AppConfig
from logbook.graph_maintenance_launcher import with_logbook_locks
from logbook.graph_maintenance_recovery import DockerRecovery


class SshMaintenance:
    """Call a pre-staged remote entrypoint using a fresh SSH connection each time."""

    def __init__(self, host: str, identity: Path, entrypoint: Path, request: dict):
        if not re.fullmatch(r"[A-Za-z0-9_.@-]+", host) or host.startswith("-"):
            raise ValueError("invalid SSH host")
        if not identity.is_absolute() or not entrypoint.is_absolute():
            raise ValueError("absolute paths required")
        self.host, self.identity, self.entrypoint, self.request = host, identity, entrypoint, request

    def invoke(self, action: str) -> str:
        if action not in {"launch", "status"}:
            raise ValueError("invalid remote action")
        command = shlex.join(["sudo", "-n", "/usr/bin/python3", str(self.entrypoint), action])
        try:
            result = subprocess.run(
                ["ssh", "-i", str(self.identity), "-o", "BatchMode=yes", "-o",
                 "ConnectTimeout=5", "-o", "StrictHostKeyChecking=yes", self.host, command],
                input=json.dumps(self.request), capture_output=True, text=True, timeout=15,
            )
        except subprocess.TimeoutExpired as error:
            raise TimeoutError("remote maintenance request timed out") from error
        if result.returncode != 0:
            raise ConnectionError("remote maintenance request failed")
        return result.stdout.strip()

    def launch(self) -> None:
        if self.invoke("launch") != "LAUNCHED":
            raise ConnectionError("unexpected remote launch response")

    def recovered(self) -> bool:
        return self.invoke("status") == "RECOVERED"


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
