"""Experimental graph maintenance launcher; not authorized for live use."""
from __future__ import annotations

import fcntl
import os
import stat
import tempfile
import time
from pathlib import Path
from typing import Callable, Protocol, TypeVar

from logbook.config import AppConfig
from logbook.graph_maintenance_recovery import DockerRecovery, drain_transactions
from logbook.graph_write_lock import GraphWriteLock

T = TypeVar("T")


class StartupRejected(ValueError):
    """Validation rejected startup before any remote state was changed."""


def with_logbook_lock_descriptors(
    config: AppConfig, operation: Callable[[int, int], T], *, allow_pending: bool = False
) -> T:
    """Hold both Mimir locks and lend their descriptors to the caller."""
    config.processing_root.mkdir(parents=True, exist_ok=True)
    with (config.processing_root / "pipeline.lock").open("a") as pipeline:
        fcntl.flock(pipeline, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with GraphWriteLock(config.processing_root, allow_pending=allow_pending) as graph:
            return operation(pipeline.fileno(), graph.fileno())


def with_logbook_locks(config: AppConfig, operation: Callable[[], T]) -> T:
    """Hold both Mimir locks until the caller's entire operation returns."""
    return with_logbook_lock_descriptors(config, lambda _pipeline, _graph: operation())

class LaunchOperations(Protocol):
    def verify_target(self) -> None: ...
    def prepare_worker(self) -> None: ...
    def schedule_recovery(self) -> None: ...
    def install_fence(self) -> None: ...
    def verify_fence(self) -> None: ...
    def drain_existing(self) -> None: ...
    def start_worker(self) -> None: ...


def launch(operations: LaunchOperations) -> None:
    operations.verify_target()
    operations.prepare_worker()
    operations.schedule_recovery()
    operations.install_fence()
    operations.verify_fence()
    operations.drain_existing()
    operations.verify_fence()
    operations.start_worker()


class DockerLaunch:
    """Launch a gated worker and independent, lock-serialized recovery timer.

    A caller must supply a fresh root-private directory and trusted root-owned
    worker and recovery files. Local privileged writers and remote
    Logbook lock coordination are not covered by this prototype.
    """

    def __init__(self, recovery: DockerRecovery, private_dir: Path,
                 worker: Path, recovery_file: Path, seconds: int,
                 recovery_worker: Path | None = None):
        if not 10 <= seconds <= 3600:
            raise ValueError("maintenance deadline out of bounds")
        self.recovery, self.private_dir = recovery, private_dir
        self.worker, self.recovery_file, self.seconds = worker, recovery_file, seconds
        self.recovery_worker = recovery_worker
        self.gate = private_dir / "worker-start"
        self.lock_path = private_dir / "setup.lock"
        self.timer = recovery.unit.removesuffix(".service") + "-recovery"
        self.deadline = 0.0
        self.after_schedule: Callable[[], None] | None = None

    def cancelled(self) -> bool:
        """Recognize only the private, atomic marker for a never-started window."""
        try:
            fd = os.open(self.lock_path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return False
        try:
            info = os.fstat(fd)
            return (stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                    and info.st_mode & 0o077 == 0 and os.read(fd, 32) == b"cancelled-before-start\n"
                    and not os.path.lexists(self.gate))
        finally:
            os.close(fd)

    def cancel_unstarted(self, prepare_claimed: Callable[[], None] | None = None) -> bool:
        """Cancel an unstarted window only after exclusive, verified preparation."""
        with tempfile.NamedTemporaryFile(dir=self.private_dir, prefix=".cancel-", mode="w+b") as marker:
            marker.write(b"cancelled-before-start\n")
            marker.flush()
            os.fsync(marker.fileno())
            try:
                os.link(marker.name, self.lock_path, follow_symlinks=False)
            except FileExistsError:
                if self.cancelled():
                    return True
                if prepare_claimed is None:
                    return False
                fd = os.open(self.lock_path, os.O_RDWR | os.O_NOFOLLOW)
                try:
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                        raise ValueError("untrusted setup lock")
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        return False
                    if self.cancelled():
                        return True
                    if os.path.lexists(self.gate):
                        return False
                    prepare_claimed()
                    if os.path.lexists(self.gate):
                        raise RuntimeError("worker gate appeared during cancellation")
                    os.lseek(fd, 0, os.SEEK_SET)
                    os.ftruncate(fd, 0)
                    if os.write(fd, b"cancelled-before-start\n") != len(b"cancelled-before-start\n"):
                        raise RuntimeError("incomplete cancellation marker")
                    os.fsync(fd)
                finally:
                    os.close(fd)
        return True

    def preflight(self) -> None:
        """Check target and trusted files without creating a worker or fence."""
        directory = self.private_dir.stat()
        if directory.st_uid != 0 or directory.st_mode & 0o077:
            raise ValueError("maintenance directory must be root-private")
        for path in (self.worker, self.recovery_file, *([self.recovery_worker] if self.recovery_worker else [])):
            info = path.stat()
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError("maintenance files must be trusted and root-owned")
        if self.gate.exists() or self.lock_path.exists():
            raise ValueError("maintenance directory is not fresh")
        self.verify_target()

    def run_window(self) -> None:
        try:
            self.preflight()
        except Exception as error:
            raise StartupRejected(str(error)) from error
        fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if self.cancelled():
                raise StartupRejected("repair window was cancelled")
            self.deadline = time.monotonic() + self.seconds
            original_run = self.recovery.run
            def run_with_setup_lock(*args, **kwargs):
                return original_run(*args, **kwargs, pass_fds=(lock.fileno(),))
            self.recovery.run = run_with_setup_lock
            try:
                launch(self)
            finally:
                self.recovery.run = original_run

    def verify_target(self) -> None:
        result = self.recovery.run(["docker", "inspect", "--format",
                                    "{{.Id}} {{.State.Pid}} {{.State.Running}}",
                                    self.recovery.container_id]).strip()
        if result != f"{self.recovery.container_id} {self.recovery.pid} true":
            raise RuntimeError("target container changed")

    def prepare_worker(self) -> None:
        if self.gate.exists():
            raise RuntimeError("worker gate already exists")
        code = ("import os,sys,time\n"
                "while not os.path.exists(sys.argv[1]) and time.monotonic()<float(sys.argv[2]): time.sleep(0.1)\n"
                "if time.monotonic()>=float(sys.argv[2]): sys.exit(1)\n"
                "os.execv(sys.argv[3],sys.argv[3:])")
        self.recovery.run(["systemd-run", "--quiet", "--unit=" + self.recovery.unit,
                           "--property=RemainAfterExit=yes",
                           "/usr/bin/python3", "-c", code, str(self.gate),
                           str(self.deadline), str(self.worker)])

    def _recovery_command(self) -> list[str]:
        code = ("import importlib.util,sys;from pathlib import Path;"
                "Path(sys.argv[8]).read_bytes()==b'cancelled-before-start\\n' and sys.exit(0);"
                "s=importlib.util.spec_from_file_location('recovery',sys.argv[1]);"
                "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
                "m.recover(m.DockerRecovery(sys.argv[2],int(sys.argv[3]),int(sys.argv[4]),sys.argv[5],Path(sys.argv[7])),"
                "recovery_worker=sys.argv[6] or None)")
        return ["/usr/bin/flock", "-x", str(self.lock_path), "/usr/bin/python3", "-c", code,
                str(self.recovery_file), self.recovery.container_id, str(self.recovery.pid),
                str(self.recovery.port), self.recovery.unit,
                str(self.recovery_worker) if self.recovery_worker else "",
                str(self.private_dir / "stopped.json"), str(self.lock_path)]

    def schedule_recovery(self) -> None:
        self.recovery.run(["systemd-run", "--quiet", "--unit=" + self.timer,
                           "--on-active=" + str(self.seconds) + "s",
                           "--timer-property=AccuracySec=1ms", "--property=RemainAfterExit=yes",
                           "--property=TimeoutStartSec=240s", *self._recovery_command()])
        if self.after_schedule is not None:
            self.after_schedule()

    def retry_recovery(self) -> None:
        """Start a new recovery service only after the original worker was stopped."""
        if not self.recovery._proof_valid():
            raise RuntimeError("valid stop proof required; fence remains")
        for path in (self.recovery_file, *([self.recovery_worker] if self.recovery_worker else [])):
            info = path.stat()
            if path.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError("maintenance files must be trusted and root-owned")
        with self.lock_path.open("r") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.recovery.verify_target()
            service = self.timer + ".service"
            state = self.recovery.run(["systemctl", "show", service,
                                       "--property=ActiveState", "--value"]).strip()
            if state not in {"inactive", "failed"}:
                raise RuntimeError("recovery service is still active")
            if state == "failed":
                self.recovery.run(["systemctl", "reset-failed", service])
            self.recovery.run(["systemd-run", "--quiet", "--unit=" + self.timer,
                               "--property=RemainAfterExit=yes", "--property=TimeoutStartSec=240s",
                               *self._recovery_command()])

    def install_fence(self) -> None:
        self.recovery.install_fence()

    def verify_fence(self) -> None:
        self.recovery.verify_target()
        for unit in (self.recovery.unit, self.timer + ".timer"):
            state = self.recovery.run(["systemctl", "show", unit,
                                       "--property=ActiveState", "--value"]).strip()
            if state != "active":
                raise RuntimeError(f"maintenance unit is not active: {unit}")

    def start_worker(self) -> None:
        if time.monotonic() >= self.deadline:
            raise RuntimeError("maintenance deadline elapsed before worker start")
        fd = os.open(self.gate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)

    def drain_existing(self) -> None:
        drain_transactions(self.recovery)
