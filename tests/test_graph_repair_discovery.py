from unittest import TestCase
from unittest.mock import patch


class GraphRepairDiscoveryTests(TestCase):
    def test_discovery_binds_target_before_and_after_bounded_query(self):
        from logbook.graph_repair_discovery import discover
        with patch('logbook.graph_repair_discovery.DockerRecovery.run',side_effect=['a'*64+' 42 true','[2]']) as run, \
             patch('logbook.graph_repair_discovery.verify_memgraph_binding') as verify:
            self.assertEqual(discover([1,2]),{'container_id':'a'*64,'pid':42,'missing_job_ids':[2]})
            self.assertEqual(verify.call_count,2)
            self.assertIn('nsenter',run.call_args_list[1].args[0][0])
        for outputs in (['bad'],['a'*64+' 42 true','[3]'],['a'*64+' 42 true','bad']):
            with patch('logbook.graph_repair_discovery.DockerRecovery.run',side_effect=outputs), \
                 patch('logbook.graph_repair_discovery.verify_memgraph_binding'):
                with self.assertRaises(ValueError):
                    discover([1,2])

    def test_reports_only_missing_requested_job_identifiers(self):
        from logbook.graph_repair_discovery import missing_jobs
        class Session:
            def run(self, query, **kwargs):
                self.query = query
                self.ids = kwargs['ids']
                return [{'id':'logbook:job:000001','count':1},{'id':'logbook:job:000004','count':1}]
        session = Session()
        self.assertEqual(missing_jobs(session,[1,2,4]),[2])
        self.assertIn('LogbookJob',session.query)
        self.assertEqual(session.ids,['logbook:job:000001','logbook:job:000002','logbook:job:000004'])

    def test_rejects_ambiguous_or_unrequested_graph_results_and_invalid_ids(self):
        from logbook.graph_repair_discovery import missing_jobs, parse_job_ids
        class Session:
            def __init__(self,rows): self.rows = rows
            def run(self,*args,**kwargs): return self.rows
        for ids in ([],[True],[0],[2,1],[1,1],list(range(1,102))):
            with self.assertRaises(ValueError):
                parse_job_ids({'job_ids':ids})
        for rows in ([{'id':'logbook:job:000001','count':2}],
                     [{'id':'logbook:job:000009','count':1}],
                     [{'id':'logbook:job:000001','count':1},{'id':'logbook:job:000001','count':1}]):
            with self.assertRaises(ValueError):
                missing_jobs(Session(rows),[1,2])
