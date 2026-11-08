import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from logbook.plan_producer import produce_request, request_generated_plan
from logbook.plan_attestation import verify_plan


class PlanProducerTests(TestCase):
    def test_real_ledger_producer_response_is_automatically_verified(self):
        from tests.test_memory_graph import _seed_memory_fixture
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = _seed_memory_fixture(root)
            key = root/'producer'
            subprocess.run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True)
            allowed = root/'allowed'
            allowed.write_text('producer '+key.with_suffix('.pub').read_text())
            allowed.chmod(0o600)
            response = produce_request(config, key, b'{"job_id":1}')
            def invoke(argv, **kwargs):
                self.assertEqual(argv, ['/usr/bin/sudo','-n','-u','logbook-plan-producer',
                                        '/usr/local/libexec/logbook-plan-producer'])
                self.assertEqual(json.loads(kwargs['input']), {'job_id':1})
                return SimpleNamespace(returncode=0, stdout=response, stderr=b'')
            with patch('logbook.plan_producer.subprocess.run', side_effect=invoke):
                plan, proof = request_generated_plan(1)
            verify_plan(plan, 1, proof, allowed)
            self.assertTrue(any(node['id']=='logbook:job:000001' for node in plan['nodes']))

    def test_producer_rejects_caller_supplied_plan_and_invalid_jobs(self):
        for raw in (b'{}', b'{"job_id":true}', b'{"job_id":0}',
                    b'{"job_id":1,"plan":{}}', b'bad', b'x'*1025):
            with self.subTest(raw=raw[:40]), self.assertRaises(ValueError):
                produce_request(None, Path('/unused'), raw)

    def test_client_fails_closed_on_service_errors(self):
        for result in (SimpleNamespace(returncode=1,stdout=b'',stderr=b'failure'),
                       SimpleNamespace(returncode=0,stdout=b'{}',stderr=b'')):
            with self.subTest(result=result), patch('logbook.plan_producer.subprocess.run',return_value=result), \
                 self.assertRaises((ValueError, RuntimeError)):
                request_generated_plan(1)

    def test_bounds_and_malformed_service_responses_fail_closed(self):
        with self.assertRaisesRegex(ValueError, 'job ID'):
            request_generated_plan(0)
        for output in (b'not-json', b'x'*5000001):
            with self.subTest(size=len(output)), \
                 patch('logbook.plan_producer.subprocess.run',
                       return_value=SimpleNamespace(returncode=0,stdout=output,stderr=b'')), \
                 self.assertRaises(ValueError):
                request_generated_plan(1)
        with patch('logbook.plan_producer.issue_plan', return_value=({'payload':'x'*5000001}, {})), \
             self.assertRaisesRegex(ValueError, 'too large'):
            produce_request(None, Path('/unused'), b'{"job_id":1}')

    def test_preparation_delivers_real_generated_proof_to_repair_client(self):
        from tests.test_memory_graph import _seed_memory_fixture
        from logbook.operator_entry import prepare_named, _named_remote
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = _seed_memory_fixture(root)
            key = root/'producer'
            subprocess.run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True)
            allowed = root/'allowed'
            allowed.write_text('producer '+key.with_suffix('.pub').read_text())
            allowed.chmod(0o600)
            options={'remote_parent':'/var/lib/logbook-graph-guard/bundles',
                     'journal_dir':'/var/lib/logbook-graph-guard/journals',
                     'container_id':'a'*64,'pid':42,'revision':'b'*40,'wheel_dir':'/wheels',
                     'host':'graph','identity':'/key','seconds':30}
            state=root/'state.json'
            response = produce_request(config,key,b'{"job_id":1}')
            def invoke(_argv, **kwargs):
                self.assertEqual(json.loads(kwargs['input']), {'job_id': 1})
                return SimpleNamespace(returncode=0,stdout=response,stderr=b'')
            with patch('logbook.plan_producer.subprocess.run',side_effect=invoke), \
                 patch('logbook.operator_entry.verify_checkout'), \
                 patch('logbook.operator_entry.build_release_bundle',return_value=b'bundle'), \
                 patch('logbook.operator_entry.provision_named_bundle'):
                self.assertEqual(prepare_named(config,1,options,state),'prepared')
            saved=json.loads(state.read_text())
            verify_plan(saved['plan'],1,saved['plan_proof'],allowed)
            remote=_named_remote(state)
            with patch.object(remote,'invoke',return_value='STAGED') as stage:
                remote.stage(saved['plan'])
            self.assertEqual(stage.call_args.args[1]['plan_proof'],saved['plan_proof'])
