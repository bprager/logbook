from unittest import TestCase
from uuid import uuid4

from logbook.guarded_graph_repair import _is_canonical_node, _is_canonical_relationship, apply_batch
from logbook.memory_graph import MemoryNode, MemoryRelationship


class RepairIdentityTests(TestCase):
    def test_node_identifier_requires_its_canonical_record_type(self):
        job = MemoryNode('logbook:job:000123', ('LogbookJob',), {'project': 'logbook'})
        self.assertTrue(_is_canonical_node(job))
        self.assertFalse(_is_canonical_node(MemoryNode(job.id, ('Topic',), job.properties)))
        note = MemoryNode('logbook:job:000123:note:routed-note:1234567890',
                          ('GeneratedNote', 'SourceEvidence'), {'project': 'logbook'})
        self.assertTrue(_is_canonical_node(note))
        self.assertFalse(_is_canonical_node(MemoryNode(note.id, ('SourceEvidence',), note.properties)))

    def test_apply_rejects_wrong_record_type_before_writes(self):
        node = MemoryNode('logbook:job:000123', ('Topic',), {'project': 'logbook'})
        with self.assertRaises(ValueError):
            apply_batch(None, str(uuid4()), [node], [])

    def test_relationship_identifier_binds_both_endpoints(self):
        rel = MemoryRelationship('logbook:topic:one:supported-by:logbook:job:000123:evidence:segment:0000',
                                 'logbook:topic:one', 'SUPPORTED_BY',
                                 'logbook:job:000123:evidence:segment:0000')
        self.assertTrue(_is_canonical_relationship(rel))
        altered = MemoryRelationship(rel.id, rel.start_id, rel.type, 'logbook:job:000124')
        self.assertFalse(_is_canonical_relationship(altered))

    def test_apply_rejects_link_identifier_with_different_endpoint_before_writes(self):
        node = MemoryNode('logbook:job:000123', ('LogbookJob',), {'project': 'logbook'})
        rel = MemoryRelationship('logbook:job:000123:supported-by:logbook:topic:one',
                                 node.id, 'SUPPORTED_BY', 'logbook:topic:other')
        with self.assertRaises(ValueError):
            apply_batch(None, str(uuid4()), [node], [rel])

    def test_relationship_rejects_ambiguous_endpoint_identifier(self):
        start = 'logbook:topic:x:supported-by:logbook:topic:y'
        end = 'logbook:topic:z'
        rel = MemoryRelationship(f'{start}:supported-by:{end}', start, 'SUPPORTED_BY', end)
        self.assertFalse(_is_canonical_relationship(rel))

    def test_segment_identifier_binds_index_and_recording(self):
        rel = MemoryRelationship('logbook:job:000123:has-segment:0002',
                                 'logbook:job:000123', 'HAS_SEGMENT',
                                 'logbook:job:000123:segment:0002')
        self.assertTrue(_is_canonical_relationship(rel))
        altered = MemoryRelationship(rel.id, rel.start_id, rel.type,
                                     'logbook:job:000124:segment:0002')
        self.assertFalse(_is_canonical_relationship(altered))
