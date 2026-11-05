from __future__ import annotations

import os
import unittest
import uuid
try:
    from neo4j import GraphDatabase
    from neo4j.exceptions import TransientError
except ImportError:
    GraphDatabase = None
    TransientError = Exception

from logbook.guarded_graph_repair import _lock_endpoints, apply_batch, rollback_batch
from logbook.memory_graph import MemoryNode, MemoryRelationship


@unittest.skipUnless(
    GraphDatabase is not None and os.environ.get('LOGBOOK_TEST_GRAPH_URI'),
    'isolated graph or optional graph driver not configured',
)
class GuardedRepairIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.driver = GraphDatabase.driver(os.environ['LOGBOOK_TEST_GRAPH_URI'])
        self.session = self.driver.session()
        constraints = list(self.session.run('SHOW CONSTRAINT INFO'))
        for label in ('LogbookJob', 'LogbookRepairBatch'):
            if not any(r['label'] == label and r['properties'] == ['id']
                       and r['constraint type'] == 'unique' for r in constraints):
                self.session.run(f'CREATE CONSTRAINT ON (n:{label}) ASSERT n.id IS UNIQUE').consume()
        self.batch = str(uuid.uuid4())
        self.foreign = 'logbook:person:fixture-' + self.batch
        self.node_id = 'logbook:job:' + str(1000000 + uuid.uuid4().int % 1000000000000)
        self.rel_id = f'{self.node_id}:generated:{self.foreign}'
        self.session.run('CREATE (:Person {id: $id})', id=self.foreign).consume()
        self.node = MemoryNode(self.node_id, ('LogbookJob',), {'project': 'logbook', 'status': 'test'})
        self.rel = MemoryRelationship(self.rel_id, self.node_id, 'GENERATED', self.foreign,
                                      {'project': 'logbook'})

    def tearDown(self):
        self.session.run('MATCH (n) WHERE n.id IN $ids DETACH DELETE n',
                         ids=[self.node_id, self.foreign]).consume()
        self.session.run('MATCH (m:LogbookRepairBatch {id:$id}) DELETE m', id=self.batch).consume()
        self.session.close()
        self.driver.close()

    def apply(self):
        return apply_batch(self.session, self.batch, [self.node], [self.rel])

    def test_relationship_only_batch_can_be_rolled_back(self):
        self.session.run('CREATE (:LogbookJob {id:$id, project:"logbook"})',
                         id=self.node_id).consume()
        apply_batch(self.session, self.batch, [], [self.rel])
        rollback_batch(self.session, self.batch)
        row = self.session.run('MATCH ()-[r {id:$id}]->() RETURN count(r) AS n',
                               id=self.rel_id).single()
        self.assertEqual(row['n'], 0)
        row = self.session.run('MATCH (n) WHERE n.id IN $ids RETURN count(n) AS n',
                               ids=[self.node_id, self.foreign]).single()
        self.assertEqual(row['n'], 2)

    def test_large_batch_has_bounded_database_round_trips(self):
        base = int(self.node_id.rsplit(':', 1)[1])
        nodes = [MemoryNode('logbook:job:' + str(base + i), ('LogbookJob',),
                            {'project': 'logbook'}) for i in range(25)]
        ids = [node.id for node in nodes]
        class CountingSession:
            def __init__(self, session):
                self.session = session
                self.calls = 0
            def run(self, *args, **kwargs):
                return self.session.run(*args, **kwargs)
            def execute_write(self, callback):
                outer = self
                class CountingTransaction:
                    def __init__(self, tx):
                        self.tx = tx
                    def run(self, *args, **kwargs):
                        outer.calls += 1
                        return self.tx.run(*args, **kwargs)
                return self.session.execute_write(lambda tx: callback(CountingTransaction(tx)))
        counted = CountingSession(self.session)
        try:
            apply_batch(counted, self.batch, nodes, [])
            self.assertLessEqual(counted.calls, 12)
            rollback_batch(self.session, self.batch)
            row = self.session.run('MATCH (n) WHERE n.id IN $ids RETURN count(n) AS n',
                                   ids=ids).single()
            self.assertEqual(row['n'], 0)
        finally:
            self.session.run('MATCH (n) WHERE n.id IN $ids DETACH DELETE n', ids=ids).consume()

    def test_endpoint_lock_rejects_stale_concurrent_relationship_write(self):
        self.session.run('CREATE (:LogbookJob {id:$id})', id=self.node_id).consume()
        other = self.driver.session()
        try:
            first = self.session.begin_transaction()
            second = other.begin_transaction()
            for tx in (first, second):
                self.assertEqual(tx.run('MATCH ()-[r {id:$id}]->() RETURN count(r) AS n',
                                        id=self.rel_id).single()['n'], 0)
            _lock_endpoints(first, [self.node_id, self.foreign], self.batch)
            first.run('MATCH (a {id:$a}), (b {id:$b}) CREATE (a)-[:GENERATED {id:$id}]->(b)',
                      a=self.node_id, b=self.foreign, id=self.rel_id).consume()
            first.commit()
            with self.assertRaises(TransientError):
                second.run('MATCH (a {id:$a}), (b {id:$b}) '
                           'CREATE (a)-[:GENERATED {id:$id}]->(b)',
                           a=self.node_id, b=self.foreign, id=self.rel_id).consume()
                second.commit()
            row = self.session.run('MATCH ()-[r {id:$id}]->() RETURN count(r) AS n',
                                   id=self.rel_id).single()
            self.assertEqual(row['n'], 1)
        finally:
            other.close()

    def test_apply_and_rollback_preserve_unrelated_node(self):
        self.apply()
        rollback_batch(self.session, self.batch)
        row = self.session.run('MATCH (n) WHERE n.id IN $ids RETURN count(n) AS n',
                               ids=[self.node_id, self.foreign]).single()
        self.assertEqual(row['n'], 1)

    def test_rollback_refuses_unrelated_attachment(self):
        self.apply()
        self.session.run('MATCH (a {id:$a}), (b {id:$b}) CREATE (b)-[:OTHER]->(a)',
                         a=self.node_id, b=self.foreign).consume()
        with self.assertRaises(ValueError):
            rollback_batch(self.session, self.batch)
        row = self.session.run('MATCH (n {id:$id}) RETURN count(n) AS n', id=self.node_id).single()
        self.assertEqual(row['n'], 1)

    def test_rollback_refuses_changed_properties(self):
        self.apply()
        self.session.run('MATCH (n {id:$id}) SET n.status = "changed"', id=self.node_id).consume()
        with self.assertRaises(ValueError):
            rollback_batch(self.session, self.batch)
        row = self.session.run('MATCH (n {id:$id}) RETURN n.status AS status', id=self.node_id).single()
        self.assertEqual(row['status'], 'changed')

    def test_rollback_refuses_missing_relationship_marker(self):
        self.apply()
        self.session.run('MATCH ()-[r {id:$id}]->() REMOVE r._logbook_repair_batch',
                         id=self.rel_id).consume()
        with self.assertRaises(ValueError):
            rollback_batch(self.session, self.batch)
        row = self.session.run('MATCH (n {id:$id}) RETURN count(n) AS n', id=self.node_id).single()
        self.assertEqual(row['n'], 1)

    def test_rollback_refuses_missing_node_marker(self):
        self.apply()
        self.session.run('MATCH (n {id:$id}) REMOVE n._logbook_repair_batch',
                         id=self.node_id).consume()
        with self.assertRaises(ValueError):
            rollback_batch(self.session, self.batch)
        row = self.session.run('MATCH ()-[r {id:$id}]->() RETURN count(r) AS n', id=self.rel_id).single()
        self.assertEqual(row['n'], 1)

    def test_rollback_refuses_partial_batch_when_one_node_loses_marker(self):
        other_id = 'logbook:job:' + str(int(self.node_id.rsplit(':', 1)[1]) + 1)
        other = MemoryNode(other_id, ('LogbookJob',), {'project': 'logbook'})
        try:
            apply_batch(self.session, self.batch, [self.node, other], [self.rel])
            self.session.run('MATCH (n {id:$id}) REMOVE n._logbook_repair_batch',
                             id=other_id).consume()
            with self.assertRaises(ValueError):
                rollback_batch(self.session, self.batch)
            row = self.session.run('MATCH (n {id:$id}) RETURN count(n) AS n',
                                   id=self.node_id).single()
            self.assertEqual(row['n'], 1)
        finally:
            self.session.run('MATCH (n {id:$id}) DETACH DELETE n', id=other_id).consume()

    def test_rollback_refuses_changed_relationship(self):
        self.apply()
        self.session.run('MATCH ()-[r {id:$id}]->() SET r.project="changed"',
                         id=self.rel_id).consume()
        with self.assertRaises(ValueError):
            rollback_batch(self.session, self.batch)
        row = self.session.run('MATCH ()-[r {id:$id}]->() RETURN r.project AS project',
                               id=self.rel_id).single()
        self.assertEqual(row['project'], 'changed')

    def test_failed_apply_does_not_leave_partial_batch(self):
        existing_id = 'logbook:job:' + str(int(self.node_id.rsplit(':', 1)[1]) + 1)
        other = MemoryNode(existing_id, ('LogbookJob',), {'project': 'logbook'})
        self.session.run('CREATE (:LogbookJob {id:$id})', id=existing_id).consume()
        try:
            with self.assertRaises(ValueError):
                apply_batch(self.session, self.batch, [self.node, other], [])
            row = self.session.run('MATCH (n {id:$id}) RETURN count(n) AS n',
                                   id=self.node_id).single()
            self.assertEqual(row['n'], 0)
        finally:
            self.session.run('MATCH (n {id:$id}) DELETE n', id=existing_id).consume()

    def test_rejects_node_without_database_uniqueness_constraint(self):
        node = MemoryNode('logbook:topic:fixture-' + self.batch, ('Topic',),
                          {'project': 'logbook'})
        with self.assertRaises(ValueError):
            apply_batch(self.session, self.batch, [node], [])
        row = self.session.run('MATCH (n {id:$id}) RETURN count(n) AS n', id=node.id).single()
        self.assertEqual(row['n'], 0)

    def test_validation_rejects_unsafe_input(self):
        with self.assertRaises(ValueError):
            apply_batch(self.session, self.batch.upper(), [self.node], [])
        with self.assertRaises(ValueError):
            apply_batch(self.session, self.batch, [], [])
        with self.assertRaises(ValueError):
            apply_batch(self.session, self.batch, [self.node, self.node], [])
        invalid = MemoryNode(self.node_id, ('Unsafe-Label',), {})
        with self.assertRaises(ValueError):
            apply_batch(self.session, self.batch, [invalid], [])
        invalid_rel = MemoryRelationship(self.rel_id, self.node_id, 'UNSAFE', self.foreign, {})
        with self.assertRaises(ValueError):
            apply_batch(self.session, self.batch, [self.node], [invalid_rel])

    def test_repeat_batch_is_rejected(self):
        self.apply()
        with self.assertRaises(ValueError):
            self.apply()

    def test_existing_relationship_id_is_not_overwritten(self):
        self.session.run('MATCH (n {id:$id}) CREATE (n)-[:OTHER {id:$rid}]->(n)',
                         id=self.foreign, rid=self.rel_id).consume()
        with self.assertRaises(ValueError):
            self.apply()
        row = self.session.run('MATCH (n {id:$id}) RETURN count(n) AS n', id=self.node_id).single()
        self.assertEqual(row['n'], 0)

    def test_missing_endpoint_rolls_back_creation(self):
        rel = MemoryRelationship(f'{self.node_id}:generated:logbook:person:missing', self.node_id,
                                 'GENERATED', 'logbook:person:missing', {})
        with self.assertRaises(ValueError):
            apply_batch(self.session, self.batch, [self.node], [rel])
        row = self.session.run('MATCH (n {id:$id}) RETURN count(n) AS n', id=self.node_id).single()
        self.assertEqual(row['n'], 0)

    def test_missing_or_changed_manifest_refuses_rollback(self):
        with self.assertRaises(ValueError):
            rollback_batch(self.session, self.batch)
        self.apply()
        self.session.run('MATCH (m:LogbookRepairBatch {id:$id}) SET m.nodes=[]',
                         id=self.batch).consume()
        with self.assertRaises(ValueError):
            rollback_batch(self.session, self.batch)
        row = self.session.run('MATCH (n {id:$id}) RETURN count(n) AS n', id=self.node_id).single()
        self.assertEqual(row['n'], 1)

    def test_supports_existing_relates_to_relationship_type(self):
        rel = MemoryRelationship(f'{self.node_id}:relates-to:{self.foreign}',
                                 self.node_id, 'RELATES_TO', self.foreign,
                                 {'project': 'logbook'})
        apply_batch(self.session, self.batch, [self.node], [rel])
        rollback_batch(self.session, self.batch)
        row = self.session.run('MATCH (n {id:$id}) RETURN count(n) AS n', id=self.node_id).single()
        self.assertEqual(row['n'], 0)

    def test_existing_id_is_never_overwritten(self):
        self.session.run('CREATE (:LogbookJob {id:$id, status:"existing"})', id=self.node_id).consume()
        with self.assertRaises(ValueError):
            self.apply()
        row = self.session.run('MATCH (n {id:$id}) RETURN n.status AS status', id=self.node_id).single()
        self.assertEqual(row['status'], 'existing')
