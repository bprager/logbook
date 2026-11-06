"""Experimental fenced repair worker; not a production activation command."""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

from logbook.config import MemgraphConfig
from logbook.graph_maintenance_recovery import DockerRecovery
from logbook.graph_write_lock import GraphWriteLock
from logbook.guarded_graph_repair import rollback_batch
from logbook.memory_graph import MemoryGraphPlan, MemoryNode, MemoryRelationship, Neo4jMemgraphClient
from logbook.repair_window_worker import recover_in_window, repair_in_window


def private_directory(path: Path) -> None:
    info = path.lstat()
    if not path.is_absolute() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('private directory required')


def read_private_json(path: Path) -> dict:
    private_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'r', encoding='utf-8') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_size > 5000000:
            raise ValueError('private bounded file required')
        value = json.load(source)
    if not isinstance(value, dict):
        raise ValueError('JSON object required')
    return value


def load_plan(path: Path) -> MemoryGraphPlan:
    raw = read_private_json(path)
    return MemoryGraphPlan(
        tuple(MemoryNode(n['id'], tuple(n['labels']), n['properties']) for n in raw['nodes']),
        tuple(MemoryRelationship(**r) for r in raw['relationships']), raw['generated_at'])



def completion_value(request: dict, batch: str) -> dict:
    return {key: request[key] for key in ('container_id', 'pid', 'port', 'unit')} | {'batch': batch}


def write_completion(journal: Path, request: dict, batch: str) -> None:
    path = journal.with_name(journal.name + '.complete')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as output:
        json.dump(completion_value(request, batch), output, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    directory = os.open(journal.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def completion_matches(journal: Path, request: dict, batch: str) -> bool:
    return read_private_json(journal.with_name(journal.name + '.complete')) == completion_value(request, batch)

def run_worker(action: str, request_file: Path) -> None:
    if os.geteuid() != 0 or action not in {'apply', 'recover'}:
        raise ValueError('root and valid action required')
    request = read_private_json(request_file)
    journal = Path(request['journal'])
    private_directory(journal.parent)
    target = DockerRecovery(request['container_id'], request['pid'], request['port'], request['unit'])
    target.verify_target()
    config = MemgraphConfig(f"bolt://127.0.0.1:{request['port']}")
    from neo4j import GraphDatabase
    with GraphDatabase.driver(config.uri, auth=None) as driver, driver.session() as session, GraphWriteLock(request_file.parent) as lock:
        if action == 'apply':
            plan = load_plan(Path(request['plan']))
            batch = repair_in_window(plan, Neo4jMemgraphClient(config), session, lock, journal)
            rollback_batch(session, batch, lock=lock, verify_only=True)
            write_completion(journal, request, batch)
        else:
            record = read_private_json(journal)
            completion = journal.with_name(journal.name + '.complete')
            if os.path.lexists(completion):
                if not completion_matches(journal, request, record['batch']):
                    raise ValueError('completion receipt does not match batch and target')
                rollback_batch(session, record['batch'], lock=lock, verify_only=True)
            else:
                recover_in_window(session, lock, journal, record['batch'])


if __name__ == '__main__':  # pragma: no cover
    if len(sys.argv) != 3:
        raise SystemExit('usage: python -m logbook.graph_maintenance_worker apply|recover REQUEST')
    run_worker(sys.argv[1], Path(sys.argv[2]))
