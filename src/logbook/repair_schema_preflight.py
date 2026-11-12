"""Read-only uniqueness checks before opening a named repair window."""
from __future__ import annotations

import json
import re
import sys
from typing import Any

from logbook.guarded_graph_repair import _is_canonical_node
from logbook.memory_graph import MemoryNode
from logbook.graph_repair_policy import MAX_PLAN_RECORDS


def check_schema(session: Any, plan: dict) -> None:
    raw_nodes = plan.get('nodes')
    if not isinstance(raw_nodes, list) or len(raw_nodes) > MAX_PLAN_RECORDS:
        raise ValueError('bounded repair nodes required')
    nodes = []
    for raw in raw_nodes:
        if not isinstance(raw, dict) or set(raw) != {'id', 'labels', 'properties'}:
            raise ValueError('invalid repair node')
        identifier, labels, properties = raw['id'], raw['labels'], raw['properties']
        if (not isinstance(identifier, str) or not isinstance(labels, list)
                or not labels or not isinstance(properties, dict)
                or any(not isinstance(label, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', label)
                       for label in labels)):
            raise ValueError('invalid repair node')
        node = MemoryNode(identifier, tuple(labels), properties)
        if not _is_canonical_node(node):
            raise ValueError('invalid repair node')
        nodes.append(node)
    present = set()
    for label in sorted({node.labels[0] for node in nodes}):
        ids = [node.id for node in nodes if node.labels[0] == label]
        rows = session.run(f'MATCH (n:{label}) WHERE n.id IN $ids RETURN n.id AS id', ids=ids)
        present.update(row['id'] for row in rows)
    required = {'LogbookRepairBatch'} | {label for node in nodes if node.id not in present for label in node.labels}
    constraints = {row['label'] for row in session.run('SHOW CONSTRAINT INFO')
                   if row['constraint type'] == 'unique' and row['properties'] == ['id']}
    missing = required - constraints
    if missing:
        raise ValueError('required node uniqueness constraints are absent: ' + ', '.join(sorted(missing)))


def main() -> None:  # pragma: no cover
    if len(sys.argv) != 2 or sys.argv[1] != '7697':
        raise ValueError('fixed graph port required')
    raw = sys.stdin.buffer.read(5000001)
    if len(raw) > 5000000:
        raise ValueError('repair plan too large')
    plan = json.loads(raw)
    if not isinstance(plan, dict):
        raise ValueError('repair plan required')
    from neo4j import GraphDatabase
    with GraphDatabase.driver('bolt://127.0.0.1:7697', auth=None, connection_timeout=5) as driver:
        with driver.session() as session:
            check_schema(session, plan)
    print('SCHEMA_OK')


if __name__ == '__main__':  # pragma: no cover
    main()
