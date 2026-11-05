"""Small, create-only graph repairs with conflict-detecting transactional rollback.

This module is not connected to the production CLI. External writers must be
quiesced before use: Logbook's graph IDs have no global uniqueness constraint.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from logbook.memory_graph import MemoryNode, MemoryRelationship, _safe_properties

_BATCH = "_logbook_repair_batch"
_DIGEST = "_logbook_repair_digest"
_LOCK = "_logbook_repair_lock"
_RESERVED = {_BATCH, _DIGEST, _LOCK}
_TYPES = {"GENERATED", "HAS_SEGMENT", "MENTIONED_IN", "RELATES_TO", "SUPPORTED_BY"}
_PREFIXES = ("logbook:job:", "logbook:topic:", "logbook:person:", "logbook:project:")


def _digest(kind: str, identity: object, props: dict[str, Any]) -> str:
    payload = json.dumps([kind, identity, props], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _check_batch(batch: str) -> None:
    if str(uuid.UUID(batch)) != batch:
        raise ValueError("batch ID must be a canonical UUID")



def _canonical_labels(identifier: str) -> tuple[str, ...] | None:
    job = r'logbook:job:[0-9]{6,}'
    patterns = (
        (job, ('LogbookJob',)),
        (job + r':note:[a-z0-9-]+:[0-9a-f]{10}', ('GeneratedNote', 'SourceEvidence')),
        (job + r':segment:[0-9]{4,}', ('TranscriptSegment',)),
        (job + r':evidence:(?:segment:[0-9]{4,}|insights)', ('SourceEvidence',)),
        (job + r':action:[0-9]{4,}:[0-9a-f]{10}', ('ActionCandidate',)),
        (job + r':decision:[0-9]{4,}:[0-9]{4,}:[0-9a-f]{10}', ('Decision',)),
        (r'logbook:topic:[a-z0-9-]+', ('Topic',)),
        (r'logbook:person:[a-z0-9-]+', ('Person',)),
        (r'logbook:project:[a-z0-9-]+', ('Project',)),
    )
    return next((labels for pattern, labels in patterns if re.fullmatch(pattern, identifier)), None)



def _is_canonical_node(node: MemoryNode) -> bool:
    labels = _canonical_labels(node.id)
    return labels is not None and sorted(node.labels) == sorted(labels)


def _is_canonical_relationship(rel: MemoryRelationship) -> bool:
    if _canonical_labels(rel.start_id) is None or _canonical_labels(rel.end_id) is None:
        return False
    if rel.type == 'HAS_SEGMENT':
        prefix = f'{rel.start_id}:segment:'
        if not rel.end_id.startswith(prefix):
            return False
        index = rel.end_id[len(prefix):]
        return bool(index and index.isascii() and index.isdigit()
                    and rel.id == f'{rel.start_id}:has-segment:{index}')
    if rel.type in _TYPES:
        slug = rel.type.lower().replace('_', '-')
        return rel.id == f'{rel.start_id}:{slug}:{rel.end_id}'
    return False


def _lock_endpoints(tx: Any, endpoint_ids: list[str], batch: str) -> None:
    """Lock endpoints in stable order without changing their committed properties."""
    for endpoint in sorted(set(endpoint_ids)):
        rows = list(tx.run('MATCH (n {id:$id}) RETURN properties(n) AS props', id=endpoint))
        if len(rows) != 1 or _LOCK in rows[0]['props']:
            raise ValueError("relationship endpoint missing, ambiguous, or reserved")
        tx.run('MATCH (n {id:$id}) SET n._logbook_repair_lock=$batch '
               'REMOVE n._logbook_repair_lock', id=endpoint, batch=batch).consume()


def apply_batch(session: Any, batch: str, nodes: list[MemoryNode],
                relationships: list[MemoryRelationship]) -> None:
    _check_batch(batch)
    if not nodes or len(nodes) + len(relationships) > 500:
        raise ValueError("batch must contain nodes and at most 500 records")
    ids = [node.id for node in nodes]
    rel_ids = [rel.id for rel in relationships]
    if (len(set(ids)) != len(ids) or len(set(rel_ids)) != len(rel_ids)
            or any(not value.startswith(_PREFIXES) for value in ids + rel_ids)):
        raise ValueError("invalid or duplicate Logbook identifiers")
    for node in nodes:
        if (not _is_canonical_node(node) or _RESERVED.intersection(node.properties)
                or 'id' in node.properties):
            raise ValueError("invalid node")
    for rel in relationships:
        if (not _is_canonical_relationship(rel) or _RESERVED.intersection(rel.properties)
                or 'id' in rel.properties):
            raise ValueError("invalid relationship")

    constraints = {
        row['label'] for row in session.run('SHOW CONSTRAINT INFO')
        if row['constraint type'] == 'unique' and row['properties'] == ['id']
    }
    if ('LogbookRepairBatch' not in constraints
            or any(label not in constraints for node in nodes for label in node.labels)):
        raise ValueError("required node uniqueness constraints are absent")

    def write(tx: Any) -> None:
        row = tx.run('MATCH (n:LogbookRepairBatch {id:$batch}) RETURN count(n) AS n',
                     batch=batch).single()
        if row['n']:
            raise ValueError("batch already exists")
        for node in nodes:
            if tx.run('MATCH (n {id:$id}) RETURN count(n) AS n', id=node.id).single()['n']:
                raise ValueError("node identifier already exists")
            props = {'id': node.id, **_safe_properties(node.properties)}
            digest = _digest('node', sorted(node.labels), props)
            props.update({_BATCH: batch, _DIGEST: digest})
            labels = ':'.join(node.labels)
            tx.run(f'CREATE (n:{labels}) SET n = $props', props=props).consume()
        _lock_endpoints(tx, [endpoint for rel in relationships
                             for endpoint in (rel.start_id, rel.end_id)], batch)
        for rel in relationships:
            if tx.run('MATCH ()-[r]->() WHERE r.id=$id RETURN count(r) AS n',
                      id=rel.id).single()['n']:
                raise ValueError("relationship identifier already exists")
            for endpoint in (rel.start_id, rel.end_id):
                if tx.run('MATCH (n {id:$id}) RETURN count(n) AS n', id=endpoint).single()['n'] != 1:
                    raise ValueError("relationship endpoint missing or ambiguous")
            props = {'id': rel.id, **_safe_properties(rel.properties)}
            digest = _digest('relationship', [rel.type, rel.start_id, rel.end_id], props)
            props.update({_BATCH: batch, _DIGEST: digest})
            tx.run(f'MATCH (a {{id:$start}}), (b {{id:$end}}) '
                   f'CREATE (a)-[r:{rel.type}]->(b) SET r=$props',
                   start=rel.start_id, end=rel.end_id, props=props).consume()
        manifest = {'id': batch, 'nodes': sorted(ids), 'relationships': sorted(rel_ids)}
        manifest['digest'] = _digest('manifest', batch, manifest)
        tx.run('CREATE (m:LogbookRepairBatch) SET m=$manifest', manifest=manifest).consume()
    session.execute_write(write)


def rollback_batch(session: Any, batch: str) -> None:
    _check_batch(batch)

    def write(tx: Any) -> None:
        # Taking write locks before checking makes later conflicting writes abort one transaction.
        manifests = list(tx.run('MATCH (m:LogbookRepairBatch {id:$batch}) '
                                'SET m._logbook_repair_lock=$batch RETURN properties(m) AS props',
                                batch=batch))
        if len(manifests) != 1:
            raise ValueError("batch manifest missing or ambiguous")
        manifest = dict(manifests[0]['props'])
        expected = manifest.pop('digest', None)
        manifest.pop(_LOCK, None)
        if _digest('manifest', batch, manifest) != expected:
            raise ValueError("batch manifest changed")
        tx.run('MATCH (n) WHERE n._logbook_repair_batch=$batch '
               'SET n._logbook_repair_lock=$batch', batch=batch).consume()
        tx.run('MATCH ()-[r]->() WHERE r._logbook_repair_batch=$batch '
               'SET r._logbook_repair_lock=$batch', batch=batch).consume()
        nodes = list(tx.run('MATCH (n) WHERE n._logbook_repair_batch=$batch '
                            'RETURN labels(n) AS labels, properties(n) AS props', batch=batch))
        rels = list(tx.run('MATCH (a)-[r]->(b) WHERE r._logbook_repair_batch=$batch '
                           'RETURN type(r) AS type, a.id AS start, b.id AS end, '
                           'properties(r) AS props', batch=batch))
        if (sorted(row['props'].get('id') for row in nodes) != manifest['nodes']
                or sorted(row['props'].get('id') for row in rels) != manifest['relationships']):
            raise ValueError("batch records missing or ambiguous")
        for row in nodes:
            props = dict(row['props'])
            expected = props.get(_DIGEST)
            actual = {k: v for k, v in props.items() if k not in _RESERVED}
            if _digest('node', sorted(row['labels']), actual) != expected:
                raise ValueError("repair node changed")
        for row in rels:
            props = dict(row['props'])
            expected = props.get(_DIGEST)
            actual = {k: v for k, v in props.items() if k not in _RESERVED}
            if _digest('relationship', [row['type'], row['start'], row['end']], actual) != expected:
                raise ValueError("repair relationship changed")
        conflicts = tx.run('MATCH (n) WHERE n._logbook_repair_batch=$batch '
                           'MATCH (n)-[r]-() WHERE r._logbook_repair_batch IS NULL '
                           'OR r._logbook_repair_batch <> $batch RETURN count(r) AS n',
                           batch=batch).single()['n']
        if conflicts:
            raise ValueError("repair node has unrelated relationships")
        tx.run('MATCH ()-[r]->() WHERE r._logbook_repair_batch=$batch DELETE r',
               batch=batch).consume()
        tx.run('MATCH (n) WHERE n._logbook_repair_batch=$batch DELETE n', batch=batch).consume()
        tx.run('MATCH (m:LogbookRepairBatch {id:$batch}) DELETE m', batch=batch).consume()
    session.execute_write(write)
