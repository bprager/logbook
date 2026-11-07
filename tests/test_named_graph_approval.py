import hashlib
import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from logbook.named_graph_approval import consume_named_approval, validate_consumed_approval


class NamedGraphApprovalTests(TestCase):
    def test_fresh_approval_is_single_use_and_bound_to_plan_and_target(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            guard=root/'guard'
            stage=root/'stage'
            guard.mkdir()
            stage.mkdir()
            guard.chmod(0o700)
            stage.chmod(0o700)
            plan={'nodes':[{'id':'one'}], 'relationships':[], 'generated_at':'now'}
            digest=hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            now=int(time.time())
            request={'container_id':'a'*64,'pid':12,'port':7697,'unit':'logbook-graph-maintenance-'+'e'*32+'.service','job_id':147,
                     'revision':'b'*40,'bundle_sha256':'c'*64,'plan_sha256':digest}
            approval={'schema':1,**request,'approved_at':now,'expires_at':now+120}
            path=guard/'approval.json'
            path.write_text(json.dumps(approval))
            path.chmod(0o600)
            consume_named_approval(request,plan,guard,stage,now=now)
            self.assertFalse(path.exists())
            self.assertTrue((stage/'approval.consumed').exists())
            (stage/'plan.json').write_text(json.dumps(plan))
            (stage/'plan.json').chmod(0o600)
            validate_consumed_approval(request,stage,now=now)
            with self.assertRaises(FileNotFoundError):
                consume_named_approval(request,plan,guard,stage,now=now)
            with self.assertRaisesRegex(ValueError,'expired'):
                validate_consumed_approval(request,stage,now=now+121)

    def test_changed_plan_target_or_old_approval_is_rejected_without_consuming(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            guard=root/'guard'
            stage=root/'stage'
            guard.mkdir()
            stage.mkdir()
            guard.chmod(0o700)
            stage.chmod(0o700)
            plan={'nodes':[], 'relationships':[], 'generated_at':'now'}
            digest=hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            now=int(time.time())
            request={'container_id':'a'*64,'pid':12,'port':7697,'unit':'logbook-graph-maintenance-'+'e'*32+'.service','job_id':147,
                     'revision':'b'*40,'bundle_sha256':'c'*64,'plan_sha256':digest}
            original={'schema':1,**request,'approved_at':now,'expires_at':now+120}
            path=guard/'approval.json'
            for change in ({'container_id':'d'*64}, {'expires_at':now-1}, {'expires_at':now+301}):
                path.write_text(json.dumps({**original,**change}))
                path.chmod(0o600)
                with self.assertRaises(ValueError):
                    consume_named_approval(request,plan,guard,stage,now=now)
                self.assertTrue(path.exists())
            path.write_text(json.dumps(original))
            path.chmod(0o600)
            with self.assertRaisesRegex(ValueError,'plan'):
                consume_named_approval(request,{'nodes':['changed']},guard,stage,now=now)
            self.assertTrue(path.exists())

    def test_bundle_marker_must_match_and_not_be_writable(self):
        from logbook.named_graph_approval import verify_bundle_marker
        with TemporaryDirectory() as tmp:
            bundle=Path(tmp)
            bundle.chmod(0o700)
            marker=bundle/'.bundle-ready'
            marker.write_text('a'*64+'\n')
            marker.chmod(0o600)
            request={'recovery_file':str(bundle/'logbook/graph_maintenance_recovery.py'), 'bundle_sha256':'a'*64}
            verify_bundle_marker(request,bundle)
            marker.write_text('b'*64+'\n')
            with self.assertRaisesRegex(ValueError,'marker'):
                verify_bundle_marker(request,bundle)
            marker.write_text('a'*64+'\n')
            marker.chmod(0o666)
            with self.assertRaisesRegex(ValueError,'marker'):
                verify_bundle_marker(request,bundle)

    def test_invalid_target_record_and_staged_plan_are_rejected(self):
        from logbook.named_graph_approval import _check, plan_digest
        with TemporaryDirectory() as tmp:
            stage=Path(tmp)
            now=int(time.time())
            plan={'nodes':[]}
            request={'container_id':'a'*64,'pid':12,'port':7697,
                     'unit':'logbook-graph-maintenance-'+'e'*32+'.service','job_id':1,
                     'revision':'b'*40,'bundle_sha256':'c'*64,'plan_sha256':plan_digest(plan)}
            approval={'schema':1,**request,'approved_at':now,'expires_at':now+120}
            with self.assertRaisesRegex(ValueError,'target'):
                _check({**request,'unit':'bad'},approval,now)
            with self.assertRaisesRegex(ValueError,'record'):
                _check(request,{**approval,'schema':True},now)
            (stage/'approval.consumed').write_text(json.dumps(approval))
            (stage/'approval.consumed').chmod(0o600)
            (stage/'plan.json').write_text(json.dumps({'nodes':['changed']}))
            (stage/'plan.json').chmod(0o600)
            with self.assertRaisesRegex(ValueError,'plan'):
                validate_consumed_approval(request,stage,now=now)
            with self.assertRaisesRegex(ValueError,'path'):
                from logbook.named_graph_approval import verify_bundle_marker
                verify_bundle_marker({'recovery_file':'/wrong'},stage)
