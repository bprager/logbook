"""Fail-closed recovery for an explicitly fenced Memgraph maintenance window.

This helper is not a maintenance-window launcher. Its caller must install and
independently schedule recovery before starting the worker or applying a fence.
"""
from __future__ import annotations

import csv
import io
import json
import re
import subprocess
import time
from typing import Protocol


class RecoveryOperations(Protocol):
    def verify_target(self) -> None: ...
    def stop_worker(self) -> None: ...
    def transactions(self) -> list[str]: ...
    def terminate(self, transaction_id: str) -> None: ...
    def remove_fence(self) -> None: ...


def recover(operations: RecoveryOperations, attempts: int = 20) -> None:
    operations.verify_target()
    operations.stop_worker()
    for _ in range(attempts):
        active = operations.transactions()
        if not active:
            operations.verify_target()
            operations.remove_fence()
            return
        for transaction_id in active:
            operations.terminate(transaction_id)
        time.sleep(0.1)
    raise RuntimeError("transactions did not drain; fence remains")


class DockerRecovery:
    """Recover a specifically identified Linux container and systemd worker."""

    def __init__(self, container_id: str, pid: int, port: int, unit: str):
        if not re.fullmatch(r"[a-f0-9]{64}", container_id):
            raise ValueError("full container ID required")
        if pid < 1 or not 1 <= port <= 65535:
            raise ValueError("invalid target")
        if not re.fullmatch(r"logbook-graph-maintenance-[a-z0-9-]+\.service", unit):
            raise ValueError("invalid maintenance unit")
        self.container_id, self.pid, self.port, self.unit = container_id, pid, port, unit
        self.rule = ["-p", "tcp", "--dport", str(port), "!", "-s", "127.0.0.0/8",
                     "-j", "REJECT", "--reject-with", "tcp-reset"]

    @staticmethod
    def run(args: list[str], text: str | None = None) -> str:
        result = subprocess.run(args, input=text, capture_output=True, text=True,
                                check=True, timeout=10)
        return result.stdout

    def firewall(self, action: str) -> None:
        self.run(["nsenter", "-t", str(self.pid), "-n", "iptables", action, "INPUT", *self.rule])

    def verify_target(self) -> None:
        result = self.run(["docker", "inspect", "--format", "{{.Id}} {{.State.Pid}} {{.State.Running}}",
                           self.container_id]).strip()
        if result != f"{self.container_id} {self.pid} true":
            raise RuntimeError("target container changed; fence remains")
        self.firewall("-C")

    def stop_worker(self) -> None:
        state = self.run(["systemctl", "show", self.unit, "--property=LoadState", "--value"]).strip()
        if state != "loaded":
            raise RuntimeError("maintenance worker is not loaded; fence remains")
        self.run(["systemctl", "stop", self.unit])
        state = self.run(["systemctl", "show", self.unit, "--property=ActiveState", "--value"]).strip()
        if state not in {"inactive", "failed"}:
            raise RuntimeError("maintenance worker did not stop; fence remains")

    def query(self, query: str) -> list[dict[str, str]]:
        output = self.run(["docker", "exec", "-i", self.container_id, "mgconsole",
                           "--host=127.0.0.1", f"--port={self.port}", "--output_format=csv"],
                          query + ";\n")
        return list(csv.DictReader(io.StringIO(output)))

    def transactions(self) -> list[str]:
        result = []
        found_self = False
        for row in self.query("SHOW TRANSACTIONS"):
            queries = json.loads(row["query"])
            if queries == ["SHOW TRANSACTIONS"]:
                found_self = True
                continue
            transaction_id = json.loads(row["transaction_id"])
            if not isinstance(transaction_id, str) or not transaction_id.isdigit():
                raise RuntimeError("invalid transaction ID; fence remains")
            result.append(transaction_id)
        if not found_self:
            raise RuntimeError("transaction listing incomplete; fence remains")
        return result

    def terminate(self, transaction_id: str) -> None:
        if not transaction_id.isdigit():
            raise ValueError("invalid transaction ID")
        self.query(f'TERMINATE TRANSACTIONS "{transaction_id}"')

    def remove_fence(self) -> None:
        self.firewall("-D")
