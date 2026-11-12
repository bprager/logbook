from unittest import TestCase
from unittest.mock import Mock


class RepairSchemaPreflightTests(TestCase):
    def test_requires_constraints_only_for_new_nodes_and_batch(self):
        from logbook.repair_schema_preflight import check_schema
        plan = {'nodes':[
            {'id':'logbook:job:000123', 'labels':['LogbookJob'], 'properties':{}},
            {'id':'logbook:topic:existing', 'labels':['Topic'], 'properties':{}},
        ]}
        session = Mock()
        def run(query, **parameters):
            if query == 'SHOW CONSTRAINT INFO':
                return [{'constraint type':'unique', 'label':'LogbookRepairBatch', 'properties':['id']},
                        {'constraint type':'unique', 'label':'LogbookJob', 'properties':['id']}]
            if ':Topic' in query:
                return [{'id':'logbook:topic:existing'}]
            return []
        session.run.side_effect = run
        check_schema(session, plan)
        session.run.side_effect = lambda query, **kwargs: []
        with self.assertRaisesRegex(ValueError, 'uniqueness constraints'):
            check_schema(session, plan)

    def test_whole_plan_preflight_accepts_more_than_one_write_batch(self):
        from logbook.repair_schema_preflight import check_schema
        from logbook.graph_repair_policy import MAX_PLAN_RECORDS
        session = Mock()
        session.run.side_effect = lambda query, **kwargs: ([
            {'constraint type':'unique', 'label':label, 'properties':['id']}
            for label in ('LogbookRepairBatch', 'LogbookJob')
        ] if query == 'SHOW CONSTRAINT INFO' else [])
        nodes = [{'id':f'logbook:job:{number:06d}', 'labels':['LogbookJob'], 'properties':{}}
                 for number in range(1, 575)]
        check_schema(session, {'nodes':nodes})
        with self.assertRaisesRegex(ValueError, 'bounded repair nodes'):
            check_schema(session, {'nodes':nodes * (MAX_PLAN_RECORDS // len(nodes) + 1)})

    def test_rejects_invalid_labels_and_noncanonical_nodes(self):
        from logbook.repair_schema_preflight import check_schema
        for node in ({'id':'not-canonical','labels':['LogbookJob'],'properties':{}},
                     {'id':'logbook:job:000123','labels':['LogbookJob) RETURN 1'],'properties':{}}):
            with self.subTest(node=node), self.assertRaises(ValueError):
                check_schema(Mock(), {'nodes':[node]})
        for plan in ({'nodes':'invalid'}, {'nodes':[{}]}):
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                check_schema(Mock(), plan)
