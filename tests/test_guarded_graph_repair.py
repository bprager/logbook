from __future__ import annotations

import os
import unittest
import uuid
try:
    from neo4j import GraphDatabase
except ImportError:
    GraphDatabase = None

from logbook.guarded_graph_repair import apply_batch, rollback_batch
from logbook.memory_graph import MemoryNode, MemoryRelationship


@unittest.skipUnless(
    GraphDatabase is not None and os.environ.get('LOGBOOK_TEST_GRAPH_URI'),
    'isolated graph or optional graph driver not configured',
)
class GuardedRepairIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.driver = GraphDatabase.driver(os.environ['LOGBOOK_TEST_GRAPH_URI'])
        self.session = self.driver.session()
        self.batch = str(uuid.uuid4())
        self.foreign = 'fixture:foreign:' + self.batch
        self.node_id = 'logbook:job:fixture-' + self.batch
        self.rel_id = 'logbook:job:fixture-rel-' + self.batch
        self.session.run('CREATE (:FixtureForeign {id: $id})', id=self.foreign).consume()
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
        other_id = self.node_id + '-second'
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
        existing_id = self.node_id + '-existing'
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
        rel = MemoryRelationship(self.rel_id, self.node_id, 'GENERATED', 'missing', {})
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

    def test_existing_id_is_never_overwritten(self):
        self.session.run('CREATE (:LogbookJob {id:$id, status:"existing"})', id=self.node_id).consume()
        with self.assertRaises(ValueError):
            self.apply()
        row = self.session.run('MATCH (n {id:$id}) RETURN n.status AS status', id=self.node_id).single()
        self.assertEqual(row['status'], 'existing')
