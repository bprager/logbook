"""Roll back a journaled fixture batch on an already isolated graph."""
from pathlib import Path
import sys
import time

from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable
from logbook.bounded_graph_repair import rollback_one_batch
from logbook.graph_maintenance_worker import read_private_json
from logbook.graph_write_lock import GraphWriteLock
from logbook.guarded_graph_repair import rollback_batch


def rollback(journal: Path, directory: Path, port: int, mode: str = 'rollback', *, startup_attempts: int = 30) -> str:
    if mode not in {'rollback', 'retain'}:
        raise ValueError('invalid fixture recovery mode')
    if type(startup_attempts) is not int or not 1 <= startup_attempts <= 120:
        raise ValueError('bounded startup attempts required')
    record = read_private_json(journal)
    with GraphDatabase.driver(f'bolt://127.0.0.1:{port}', auth=None) as driver:
        for attempt in range(startup_attempts):
            try:
                driver.verify_connectivity()
                break
            except ServiceUnavailable:
                if attempt == startup_attempts - 1:
                    raise
                time.sleep(1)
        with driver.session() as session, GraphWriteLock(directory) as lock:
            if mode == 'retain':
                result = rollback_batch(session, record['batch'], lock=lock, verify_only=True)
                if result != 'verified':
                    raise ValueError('fixture retained batch unconfirmed')
                return 'retained'
            result = rollback_one_batch(session, lock, journal, record['batch'], allow_absent=True)
    return 'not-retained' if result == 'absent' else 'rolled-back'


if __name__ == '__main__':  # pragma: no cover
    if len(sys.argv) not in {5, 6}:
        raise SystemExit('journal, private directory, port and mode required')
    print(rollback(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4],
                   startup_attempts=int(sys.argv[5]) if len(sys.argv) == 6 else 30))
