"""Read-only comparison of one signed source plan with the named graph."""
from __future__ import annotations

from typing import Any
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))

from logbook.named_graph_approval import plan_digest  # noqa: E402
from logbook.standing_graph_policy import check_plan  # noqa: E402


def incomplete_plan(session: Any, job_id: int, plan: dict) -> bool:
    """Return whether any expected record is missing; reject conflicting IDs."""
    identity = '0'*40
    digest = '0'*64
    request = {'job_id':job_id,'revision':identity,'bundle_sha256':digest,'plan_sha256':plan_digest(plan)}
    policy = {'schema':2,'max_records':500,'expires_at':None}
    check_plan(request,plan,policy,release={'revision':identity,'bundle_sha256':digest})
    nodes = {node['id']:node for node in plan['nodes']}
    relations = {rel['id']:rel for rel in plan['relationships']}
    if (len(nodes) != len(plan['nodes']) or len(relations) != len(plan['relationships'])
            or f'logbook:job:{job_id:06d}' not in nodes):
        raise ValueError('invalid or duplicate plan identifiers')
    seen_nodes = set()
    for row in session.run('MATCH (n) WHERE n.id IN $ids RETURN n.id AS id, labels(n) AS labels', ids=list(nodes)):
        identifier = row['id']
        if identifier not in nodes or identifier in seen_nodes:
            raise ValueError('ambiguous graph node identifier')
        if not set(nodes[identifier]['labels']).issubset(row['labels']):
            raise ValueError('graph node type conflict')
        seen_nodes.add(identifier)
    seen_relations = set()
    for row in session.run('MATCH (a)-[r]->(b) WHERE r.id IN $ids '
                           'RETURN r.id AS id, type(r) AS type, a.id AS start, b.id AS end', ids=list(relations)):
        identifier = row['id']
        if identifier not in relations or identifier in seen_relations:
            raise ValueError('ambiguous graph relationship identifier')
        expected = relations[identifier]
        if (row['type'],row['start'],row['end']) != (expected['type'],expected['start_id'],expected['end_id']):
            raise ValueError('graph relationship conflict')
        seen_relations.add(identifier)
    return len(seen_nodes) != len(nodes) or len(seen_relations) != len(relations)


def discover(job_id: int, plan: dict, proof: dict) -> dict:
    """Verify producer evidence and query only the fixed, verified graph target."""
    import json
    import re
    from pathlib import Path
    from logbook.fixture_boot_guard import verify_memgraph_binding
    from logbook.graph_maintenance_recovery import DockerRecovery
    from logbook.plan_attestation import verify_plan

    verify_plan(plan,job_id,proof,Path('/var/lib/logbook-graph-guard/plan.allowed_signers'))
    output = DockerRecovery.run(['/usr/bin/docker','inspect','--format',
                                 '{{.Id}} {{.State.Pid}} {{.State.Running}}','memgraph-service']).split()
    if len(output) != 3 or not re.fullmatch(r'[a-f0-9]{64}',output[0]) or not output[1].isdigit() or output[2] != 'true':
        raise ValueError('named graph target unavailable')
    recovery = DockerRecovery(output[0],int(output[1]),7697,'logbook-graph-maintenance-discovery.service')
    verify_memgraph_binding(recovery)
    raw = recovery.run(['/usr/bin/nsenter','-t',output[1],'-n','/usr/bin/python3','-I',
                        str(Path(__file__).resolve()),'query'],json.dumps({'job_id':job_id,'plan':plan}),timeout=30)
    try:
        incomplete = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError('invalid plan discovery result') from error
    if type(incomplete) is not bool:
        raise ValueError('invalid plan discovery result')
    verify_memgraph_binding(recovery)
    return {'container_id':output[0], 'pid':int(output[1]), 'incomplete':incomplete}


def main() -> None:  # pragma: no cover
    import json
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
    if len(sys.argv) not in (1,2) or (len(sys.argv) == 2 and sys.argv[1] != 'query'):
        raise ValueError('invalid plan discovery mode')
    raw = sys.stdin.buffer.read(5000001)
    if len(raw) > 5000000:
        raise ValueError('plan discovery request too large')
    request = json.loads(raw)
    expected = {'job_id','plan'} if len(sys.argv) == 2 else {'job_id','plan','proof'}
    if not isinstance(request,dict) or set(request) != expected:
        raise ValueError('invalid plan discovery request')
    if len(sys.argv) == 2:
        from neo4j import GraphDatabase
        with GraphDatabase.driver('bolt://127.0.0.1:7697',auth=None,connection_timeout=5) as driver:
            with driver.session() as session:
                print(json.dumps(incomplete_plan(session,request['job_id'],request['plan'])))
    else:
        print(json.dumps(discover(request['job_id'],request['plan'],request['proof']),sort_keys=True))


if __name__ == '__main__':  # pragma: no cover
    main()
