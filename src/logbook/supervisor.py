"""Bounded service recovery, separate from read-only observer clients."""
from __future__ import annotations

import argparse
from contextlib import closing
import fcntl
import json
import os
import signal
import sqlite3
import subprocess
import time
from pathlib import Path
from threading import Event

from logbook.metrics import MetricSample, render_prometheus_metrics


SERVICE_NAMES = {'receiver', 'api', 'queue', 'odin'}


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class RecoveryState:
    def __init__(self, path):
        self.path = path
        # Malformed state must fail closed, never silently grant more restarts.
        self.services = json.loads(path.read_text())['services'] if path.exists() else {}

    def observe(self, name, healthy, now, restart):
        row = self.services.setdefault(name, dict(attempts=0, exhausted=False,
                                                 healthy_since=None, last_attempt=None))
        row.update(healthy=healthy, checked_at=now)
        if healthy is None:
            row.update(detail='starting', healthy_since=None)
            return
        if healthy:
            if row['healthy_since'] is None:
                row['healthy_since'] = now
            row['detail'] = 'healthy'
            if now - row['healthy_since'] >= 600:
                row.update(attempts=0, exhausted=False)
            return
        row['healthy_since'] = None
        if row['attempts'] >= 3:
            row.update(exhausted=True, detail='recovery_exhausted')
            return
        if row['last_attempt'] is not None and now - row['last_attempt'] < 60:
            return
        row['attempts'] += 1
        row['last_attempt'] = now
        row['detail'] = 'restarting'
        # Persist the consumed attempt BEFORE launching anything, including on crash.
        self.save(now)
        try:
            restart()
        except (OSError, subprocess.SubprocessError):
            row['detail'] = 'restart_failed'

    def save(self, now):
        atomic_json(self.path, {'checked_at': now, 'services': self.services})


class ManagedService:
    def __init__(self, spec, logs):
        self.spec = spec
        self.logs = logs
        self.process = None
        self.started_at = None
        self.completed_at = None
        self.last_exit = None

    def start(self, now):
        self.stop()
        if 'restart_command' in self.spec:
            with (self.logs / (self.spec['name'] + '.log')).open('ab') as stream:
                subprocess.run(self.spec['restart_command'], check=True, timeout=25,
                               stdout=stream, stderr=subprocess.STDOUT)
            self.started_at = now
            return
        log = self.logs / (self.spec['name'] + '.log')
        if log.exists() and log.stat().st_size > 5 * 1024**2:
            os.replace(log, log.with_suffix('.previous.log'))
        with log.open('ab') as stream:
            self.process = subprocess.Popen(self.spec['command'], stdout=stream,
                                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        self.started_at = now
        self.completed_at = None
        self.last_exit = None

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.process = None

    def healthy(self, now):
        if self.process is not None:
            code = self.process.poll()
            if code is not None:
                if self.completed_at is None:
                    self.completed_at, self.last_exit = now, code
                return code == 0 if self.spec.get('interval') else False
            if self.spec.get('interval'):
                return now - self.started_at < self.spec.get('timeout', 1800)
        elif 'restart_command' not in self.spec:
            return False
        # Recorder/recovery commands and read-only snapshots do not need the
        # supervisor's HTTP client just to import their CLI.
        import httpx

        try:
            response = httpx.get(self.spec['health_url'], timeout=3, trust_env=False)
            response.raise_for_status()
            payload = response.json()
            field = self.spec.get('health_field', 'status')
            return payload.get(field) == self.spec.get('health_value', 'ok')
        except (httpx.HTTPError, ValueError):
            # A live process gets a short startup grace, never an unlimited pass.
            starting = (self.started_at is not None and now - self.started_at < 90
                        and (self.process is None or self.process.poll() is None))
            return None if starting else False

    def scheduled(self, now):
        return (self.spec.get('interval') and self.completed_at is not None
                and self.last_exit == 0 and now - self.completed_at >= self.spec['interval'])


def read_supervision(processing_root, now=None):
    path = processing_root / 'services' / 'supervisor-state.json'
    try:
        data = json.loads(path.read_text())
        age = max(0, (time.time() if now is None else now) - data['checked_at'])
        services = {name: {key: row.get(key) for key in
                    ('healthy', 'attempts', 'exhausted', 'detail', 'checked_at')}
                    for name, row in data['services'].items() if name in SERVICE_NAMES}
        return {'state': 'ok' if age <= 180 else 'stale',
                'checked_at': data['checked_at'], 'services': services}
    except (OSError, ValueError, KeyError, TypeError):
        return {'state': 'unavailable', 'checked_at': 0, 'services': {}}


def supervision_summary(status):
    if not status:
        return 'Services: not configured'
    parts = []
    rows = status.get('services', {}).items()
    for name, row in sorted(rows, key=lambda item: (not item[1].get('exhausted'), bool(item[1].get('healthy')))):
        detail = ('ALARM' if row.get('exhausted') else 'starting' if row.get('detail') == 'starting'
                  else 'ok' if row.get('healthy') else 'down')
        parts.append(f"{name} {detail} ({row.get('attempts', 0)}/3)")
    return 'Recovery ' + status['state'] + ' | ' + '  '.join(parts)


def supervision_metrics(processing_root):
    status = read_supervision(processing_root)
    samples = [MetricSample('logbook_supervisor_heartbeat_timestamp_seconds', status['checked_at'])]
    for name, row in status['services'].items():
        labels = {'service': name}
        samples.extend([
            MetricSample('logbook_service_healthy', int(bool(row['healthy'])), labels),
            MetricSample('logbook_service_recovery_attempts', row['attempts'], labels),
            MetricSample('logbook_service_recovery_exhausted', int(bool(row['exhausted'])), labels),
        ])
    return samples


def queue_pending_age(ledger_path, now=None):
    """Oldest remote recording still waiting for publication; no transcript data."""
    try:
        with closing(sqlite3.connect(ledger_path.as_uri() + '?mode=ro', uri=True)) as db:
            value = db.execute('''SELECT min(julianday(j.first_seen_at)) FROM recording_jobs j
                JOIN remote_delivery d ON d.job_id=j.id WHERE j.vault_synced_at IS NULL''').fetchone()[0]
        return max(0, (time.time() if now is None else now) - (value - 2440587.5) * 86400) if value else 0
    except sqlite3.Error:
        return -1


def write_metrics(config):
    root = Path(config['root'])
    folder = root / 'metrics'
    folder.mkdir(exist_ok=True, mode=0o700)
    samples = supervision_metrics(root.parent)
    samples.append(MetricSample('logbook_remote_unpublished_age_seconds',
                                queue_pending_age(Path(config['ledger_path']))))
    temporary = folder / 'logbook.tmp'
    temporary.write_text(render_prometheus_metrics(samples))
    os.replace(temporary, folder / 'logbook.prom')


def run(config, stop):
    root = Path(config['root'])
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    logs = root / 'logs'
    logs.mkdir(exist_ok=True, mode=0o700)
    with (root / 'supervisor.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = RecoveryState(root / 'supervisor-state.json')
        services = [ManagedService(spec, logs) for spec in config['services']]
        if len({s.spec['name'] for s in services}) != len(services) or any(
                s.spec['name'] not in SERVICE_NAMES for s in services):
            raise ValueError('Invalid supervised services')
        try:
            # First installation starts local services once without charging a repair.
            # Later supervisor restarts preserve each service's existing budget.
            for service in services:
                name = service.spec['name']
                previous = state.services.get(name)
                ordinary_start = previous is None or (previous.get('healthy') and not previous['exhausted'])
                if ordinary_start and 'command' in service.spec:
                    state.services.setdefault(name, dict(attempts=0, exhausted=False,
                                                          healthy_since=None, last_attempt=None))
                    # Starting healthy services after a planned supervisor/host restart
                    # is not an outage repair. Preserve, rather than reset, its budget.
                    state.services[name].update(healthy=None, healthy_since=None)
                    state.save(time.time())
                    try:
                        service.start(time.time())
                    except OSError:
                        pass  # The normal probe below consumes the bounded repair attempt.
            while not stop.is_set():
                now = time.time()
                reset = root / 'reset-request.json'
                if reset.exists():
                    name = json.loads(reset.read_text())['service']
                    if name in SERVICE_NAMES:
                        state.services.pop(name, None)
                    reset.unlink()
                for service in services:
                    name = service.spec['name']
                    before = state.services.get(name, {}).get('detail')
                    healthy = service.healthy(now)
                    state.observe(name, healthy, now, lambda s=service: s.start(now))
                    if healthy and service.scheduled(now):
                        service.start(now)
                    row = state.services[name]
                    if before != row['detail']:
                        with (root / 'events.jsonl').open('a') as log:
                            log.write(json.dumps({'at': now, 'service': name, **row}) + '\n')
                state.save(now)
                if config.get('ledger_path'):
                    write_metrics(config)
                stop.wait(60)
        finally:
            for service in services:
                service.stop()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--reset', choices=sorted(SERVICE_NAMES))
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text())
    if args.reset:
        atomic_json(Path(config['root']) / 'reset-request.json', {'service': args.reset})
        print('Recovery reset requested:', args.reset)
        return
    stop = Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    run(config, stop)


if __name__ == '__main__':  # pragma: no cover - exercised by the installed launchd service
    main()

