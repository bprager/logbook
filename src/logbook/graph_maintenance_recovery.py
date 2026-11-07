"""Fail-closed recovery for an explicitly fenced Memgraph maintenance window.

This helper is not a maintenance-window launcher. Its caller must install and
independently schedule recovery before starting the worker or applying a fence.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import stat
import re
import subprocess
import time
from pathlib import Path
from typing import Protocol


class TransactionListingUnavailable(RuntimeError):
    """A transaction listing was incomplete or contained an unusable ID."""


class RecoveryOperations(Protocol):
    def verify_target(self) -> None: ...
    def stop_worker(self) -> None: ...
    def transactions(self) -> list[str]: ...
    def terminate(self, transaction_id: str) -> None: ...
    def remove_fence(self) -> None: ...
    def run_recovery_worker(self, worker: str) -> None: ...


def drain_transactions(operations: RecoveryOperations, attempts: int = 20) -> None:
    for _ in range(attempts):
        try:
            active = operations.transactions()
        except TransactionListingUnavailable:
            time.sleep(0.1)
            continue
        if not active:
            return
        for transaction_id in active:
            operations.terminate(transaction_id)
        time.sleep(0.1)
    raise RuntimeError("transactions did not drain; fence remains")


def recover(operations: RecoveryOperations, attempts: int = 20, *,
            recovery_worker: str | None = None) -> None:
    operations.verify_target()
    operations.stop_worker()
    drain_transactions(operations, attempts)
    operations.verify_target()
    if recovery_worker is not None:
        for retry in range(3):
            try:
                operations.run_recovery_worker(recovery_worker)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                drain_transactions(operations, attempts)
                operations.verify_target()
                if retry == 2:
                    raise
                time.sleep(0.5)
            else:
                break
        drain_transactions(operations, attempts)
        operations.verify_target()
    operations.remove_fence()


class DockerRecovery:
    """Recover a specifically identified Linux container and systemd worker."""

    def __init__(self, container_id: str, pid: int, port: int, unit: str,
                 stop_proof: Path | None = None):
        if not re.fullmatch(r"[a-f0-9]{64}", container_id):
            raise ValueError("full container ID required")
        if pid < 1 or not 1 <= port <= 65535:
            raise ValueError("invalid target")
        if not re.fullmatch(r"logbook-graph-maintenance-[a-z0-9-]+\.service", unit):
            raise ValueError("invalid maintenance unit")
        self.container_id, self.pid, self.port, self.unit = container_id, pid, port, unit
        self.stop_proof = stop_proof
        self.table = "logbook_" + hashlib.sha256(unit.encode()).hexdigest()[:16]

    @staticmethod
    def run(args: list[str], text: str | None = None, timeout: int = 10) -> str:
        result = subprocess.run(args, input=text, capture_output=True, text=True,
                                check=True, timeout=timeout)
        return result.stdout

    def install_fence(self) -> None:
        rules = (f"add table inet {self.table}\n"
                 f"add chain inet {self.table} input {{ type filter hook input priority -300; policy accept; }}\n"
                 f'add rule inet {self.table} input iifname != "lo" '
                 f"tcp dport {self.port} reject with tcp reset\n")
        self.run(["nsenter", "-t", str(self.pid), "-n", "nft", "-f", "-"], rules)

    def verify_target(self) -> None:
        result = self.run(["docker", "inspect", "--format", "{{.Id}} {{.State.Pid}} {{.State.Running}}",
                           self.container_id]).strip()
        if result != f"{self.container_id} {self.pid} true":
            raise RuntimeError("target container changed; fence remains")
        output = self.run(["nsenter", "-t", str(self.pid), "-n", "nft", "-j", "list",
                           "table", "inet", self.table])
        entries = json.loads(output)["nftables"]
        chains = [e["chain"] for e in entries if "chain" in e]
        rules = [e["rule"] for e in entries if "rule" in e]
        expected = [
            {"match": {"op": "!=", "left": {"meta": {"key": "iifname"}}, "right": "lo"}},
            {"match": {"op": "==", "left": {"payload": {"protocol": "tcp", "field": "dport"}},
                       "right": self.port}},
            {"reject": {"type": "tcp reset"}},
        ]
        if (len(chains) != 1 or len(rules) != 1 or chains[0].get("name") != "input"
                or chains[0].get("hook") != "input" or chains[0].get("prio") != -300
                or chains[0].get("policy") != "accept" or rules[0].get("expr") != expected):
            raise RuntimeError("maintenance fence changed; access remains blocked")

    def stop_worker(self) -> None:
        state = self.run(["systemctl", "show", self.unit, "--property=LoadState", "--value"]).strip()
        if state != "loaded":
            if state == "not-found" and self._proof_valid():
                return
            raise RuntimeError("maintenance worker is not loaded; fence remains")
        self.run(["systemctl", "stop", self.unit])
        state = self.run(["systemctl", "show", self.unit, "--property=ActiveState", "--value"]).strip()
        if state not in {"inactive", "failed"}:
            raise RuntimeError("maintenance worker did not stop; fence remains")
        if self.stop_proof is not None:
            self._save_proof()

    def _proof_value(self) -> dict:
        return {"container": self.container_id, "pid": self.pid,
                "port": self.port, "unit": self.unit}

    def _proof_valid(self) -> bool:
        if self.stop_proof is None:
            return False
        try:
            parent = self.stop_proof.parent.stat()
            if parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
                return False
            fd = os.open(self.stop_proof, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "r", encoding="utf-8") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                    return False
                return json.load(source) == self._proof_value()
        except (OSError, ValueError):
            return False

    def _save_proof(self) -> None:
        assert self.stop_proof is not None
        parent = self.stop_proof.parent.stat()
        if parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
            raise RuntimeError("stop proof directory is not private; fence remains")
        try:
            fd = os.open(self.stop_proof, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if not self._proof_valid():
                raise RuntimeError("invalid stop proof; fence remains")
            return
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(self._proof_value(), output)
            output.flush()
            os.fsync(output.fileno())
        directory = os.open(self.stop_proof.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

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
                raise TransactionListingUnavailable("invalid transaction ID; fence remains")
            result.append(transaction_id)
        if not found_self:
            raise TransactionListingUnavailable("transaction listing incomplete; fence remains")
        return result

    def terminate(self, transaction_id: str) -> None:
        if not transaction_id.isdigit():
            raise ValueError("invalid transaction ID")
        self.query(f'TERMINATE TRANSACTIONS "{transaction_id}"')

    def run_recovery_worker(self, worker: str) -> None:
        path = Path(worker)
        if not path.is_absolute() or path.is_symlink():
            raise ValueError("absolute trusted recovery worker required")
        info = path.stat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("recovery worker must be trusted and root-owned")
        subprocess.run([worker], check=True, timeout=60, capture_output=True)

    def remove_fence(self) -> None:
        self.run(["nsenter", "-t", str(self.pid), "-n", "nft", "delete", "table",
                  "inet", self.table])
