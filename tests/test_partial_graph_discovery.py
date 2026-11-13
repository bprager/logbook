from unittest import TestCase


class PartialGraphDiscoveryTests(TestCase):
    def test_discovers_large_source_plan_without_a_write_permission(self):
        from logbook.partial_graph_discovery import incomplete_plan
        job = 'logbook:job:000001'
        nodes = [{'id': job, 'labels': ['LogbookJob'], 'properties': {}}] + [
            {'id': job + f':segment:{i:04d}', 'labels': ['TranscriptSegment'], 'properties': {}}
            for i in range(600)]
        plan = {'nodes': nodes, 'relationships': [], 'generated_at': 'fixture'}
        class Session:
            def run(self, query, **kwargs):
                return [] if 'MATCH (a)-[r]' in query else [
                    {'id': n['id'], 'labels': n['labels']} for n in nodes]
        self.assertFalse(incomplete_plan(Session(), 1, plan))

    def test_read_only_discovery_above_write_plan_limit_keeps_write_limit(self):
        from logbook.partial_graph_discovery import incomplete_plan
        from logbook.standing_graph_policy import check_plan
        from logbook.named_graph_approval import plan_digest
        job = 'logbook:job:000001'
        nodes = [{'id': job, 'labels': ['LogbookJob'], 'properties': {}}] + [
            {'id': job + f':segment:{i:04d}', 'labels': ['TranscriptSegment'], 'properties': {}}
            for i in range(5000)]
        plan = {'nodes': nodes, 'relationships': [], 'generated_at': 'fixture'}
        class Session:
            def run(self, query, **kwargs):
                return [] if 'MATCH (a)-[r]' in query else [
                    {'id': n['id'], 'labels': n['labels']} for n in nodes]
        self.assertFalse(incomplete_plan(Session(), 1, plan))
        policy = {'schema': 3, 'max_batch_records': 500, 'max_plan_records': 5000,
                  'expires_at': None}
        release = {'revision': '0'*40, 'bundle_sha256': '0'*64}
        request = {'job_id': 1, **release, 'plan_sha256': plan_digest(plan)}
        with self.assertRaisesRegex(ValueError, 'exceeds policy'):
            check_plan(request, plan, policy, release=release)
        with self.assertRaisesRegex(ValueError, 'duplicate plan'):
            incomplete_plan(Session(), 1, {**plan, 'nodes': nodes + nodes[:1]})
        with self.assertRaisesRegex(ValueError, 'exceeds policy'):
            incomplete_plan(Session(), 1, {**plan, 'nodes': []})
        invalid = {**nodes[-1], 'id': 'another:job:1'}
        with self.assertRaisesRegex(ValueError, 'out-of-scope'):
            incomplete_plan(Session(), 1, {**plan, 'nodes': nodes[:-1] + [invalid]})

    def test_signed_discovery_checks_graph_binding_and_result(self):
        from unittest.mock import patch
        from logbook.partial_graph_discovery import discover
        with patch('logbook.plan_attestation.verify_plan') as verify, \
             patch('logbook.fixture_boot_guard.verify_memgraph_binding') as binding, \
             patch('logbook.graph_maintenance_recovery.DockerRecovery.run',side_effect=['a'*64+' 42 true','true']):
            self.assertEqual(discover(1,{'nodes':[]},{}),{'container_id':'a'*64,'pid':42,'incomplete':True})
            verify.assert_called_once()
            self.assertEqual(binding.call_count,2)
        for outputs in (['bad'],['a'*64+' 42 true','{}'],['a'*64+' 42 true','not-json']):
            with patch('logbook.plan_attestation.verify_plan'), \
                 patch('logbook.fixture_boot_guard.verify_memgraph_binding'), \
                 patch('logbook.graph_maintenance_recovery.DockerRecovery.run',side_effect=outputs):
                with self.assertRaises(ValueError):
                    discover(1,{'nodes':[]},{})

    def test_detects_missing_records_even_when_job_exists(self):
        from logbook.partial_graph_discovery import incomplete_plan
        job='logbook:job:000001'
        segment=job+':segment:0000'
        plan={'nodes':[{'id':job,'labels':['LogbookJob'],'properties':{}},
                       {'id':segment,'labels':['TranscriptSegment'],'properties':{}}],
              'relationships':[{'id':job+':has-segment:0000','type':'HAS_SEGMENT',
                                'start_id':job,'end_id':segment,'properties':{}}], 'generated_at':'fixture'}
        class Session:
            def __init__(self,nodes,relationships): self.nodes,self.relationships=nodes,relationships
            def run(self,query,**kwargs):
                return self.relationships if 'MATCH (a)-[r]' in query else self.nodes
        nodes=[{'id':job,'labels':['LogbookJob']},{'id':segment,'labels':['TranscriptSegment']}]
        rels=[{'id':job+':has-segment:0000','type':'HAS_SEGMENT','start':job,'end':segment}]
        self.assertTrue(incomplete_plan(Session(nodes[:1],[]),1,plan))
        self.assertTrue(incomplete_plan(Session(nodes,[]),1,plan))
        self.assertFalse(incomplete_plan(Session(nodes,rels),1,plan))
        with self.assertRaisesRegex(ValueError,'ambiguous'):
            incomplete_plan(Session(nodes+nodes[:1],rels),1,plan)
        with self.assertRaisesRegex(ValueError,'conflict'):
            incomplete_plan(Session(nodes,[{**rels[0],'end':job}]),1,plan)
        with self.assertRaisesRegex(ValueError,'type conflict'):
            incomplete_plan(Session([{**nodes[0],'labels':['Wrong']}],[]),1,plan)
        with self.assertRaisesRegex(ValueError,'ambiguous graph relationship'):
            incomplete_plan(Session(nodes,rels+rels),1,plan)
        with self.assertRaisesRegex(ValueError,'duplicate plan'):
            incomplete_plan(Session([],[]),1,{**plan,'nodes':plan['nodes']+plan['nodes'][:1]})
