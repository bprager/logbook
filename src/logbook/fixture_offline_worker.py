"""Roll back a journaled fixture batch on an already isolated graph."""
from pathlib import Path
import sys
import time

from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable
from logbook.bounded_graph_repair import rollback_one_batch
from logbook.graph_maintenance_worker import read_private_json
from logbook.graph_write_lock import GraphWriteLock


def rollback(journal: Path, directory: Path, port: int) -> str:
    record = read_private_json(journal)
    with GraphDatabase.driver(f'bolt://127.0.0.1:{port}', auth=None) as driver:
        for attempt in range(30):
            try:
                driver.verify_connectivity()
                break
            except ServiceUnavailable:
                if attempt == 29:
                    raise
                time.sleep(1)
        with driver.session() as session, GraphWriteLock(directory) as lock:
            result = rollback_one_batch(session, lock, journal, record['batch'], allow_absent=True)
    return 'not-retained' if result == 'absent' else 'rolled-back'


if __name__ == '__main__':  # pragma: no cover
    if len(sys.argv) != 4:
        raise SystemExit('journal, private directory and port required')
    print(rollback(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])))
