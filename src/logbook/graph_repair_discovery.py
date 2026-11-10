"""Bounded, read-only discovery for missing Logbook job nodes."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))

from logbook.fixture_boot_guard import verify_memgraph_binding  # noqa: E402
from logbook.graph_maintenance_recovery import DockerRecovery  # noqa: E402


def parse_job_ids(request: dict) -> list[int]:
    if (not isinstance(request,dict) or set(request) != {'job_ids'}
            or not isinstance(request['job_ids'],list) or not 1 <= len(request['job_ids']) <= 100):
        raise ValueError('bounded job IDs required')
    ids = request['job_ids']
    if (any(type(value) is not int or not 1 <= value <= 9223372036854775807 for value in ids)
            or ids != sorted(set(ids))):
        raise ValueError('invalid job IDs')
    return ids


def missing_jobs(session: Any, ids: list[int]) -> list[int]:
    ids = parse_job_ids({'job_ids':ids})
    names = [f'logbook:job:{value:06d}' for value in ids]
    rows = session.run('MATCH (n:LogbookJob) WHERE n.id IN $ids RETURN n.id AS id, count(n) AS count', ids=names)
    present = set()
    for row in rows:
        identifier = row['id']
        if identifier not in names or identifier in present or row['count'] != 1:
            raise ValueError('ambiguous graph job identifiers')
        present.add(identifier)
    return [value for value,name in zip(ids,names) if name not in present]


def discover(ids: list[int]) -> dict:
    ids = parse_job_ids({'job_ids':ids})
    output = DockerRecovery.run(['/usr/bin/docker','inspect','--format',
                                 '{{.Id}} {{.State.Pid}} {{.State.Running}}','memgraph-service']).split()
    if len(output) != 3 or not re.fullmatch(r'[a-f0-9]{64}',output[0]) or not output[1].isdigit() or output[2] != 'true':
        raise ValueError('named graph target unavailable')
    recovery = DockerRecovery(output[0],int(output[1]),7697,'logbook-graph-maintenance-discovery.service')
    verify_memgraph_binding(recovery)
    result = recovery.run(['/usr/bin/nsenter','-t',output[1],'-n','/usr/bin/python3','-I',
                           str(Path(__file__).resolve()),'query'],json.dumps({'job_ids':ids}),timeout=30)
    try:
        missing = json.loads(result)
        if not isinstance(missing,list) or any(value not in ids for value in missing):
            raise ValueError('invalid graph discovery response')
        if missing:
            parse_job_ids({'job_ids':missing})
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError('invalid graph discovery response') from error
    verify_memgraph_binding(recovery)
    return {'container_id':output[0],'pid':int(output[1]),'missing_job_ids':missing}


def main() -> None:  # pragma: no cover
    if len(sys.argv) not in (1,2) or (len(sys.argv) == 2 and sys.argv[1] != 'query'):
        raise SystemExit('invalid discovery mode')
    raw = sys.stdin.buffer.read(4097)
    if len(raw) > 4096:
        raise ValueError('discovery request too large')
    ids = parse_job_ids(json.loads(raw))
    if len(sys.argv) == 2:
        from neo4j import GraphDatabase
        with GraphDatabase.driver('bolt://127.0.0.1:7697',auth=None,connection_timeout=5) as driver:
            with driver.session() as session:
                print(json.dumps(missing_jobs(session,ids)))
    else:
        print(json.dumps(discover(ids),sort_keys=True))


if __name__ == '__main__':  # pragma: no cover
    main()
