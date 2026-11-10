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
    def test_bounded_job_discovery_exposes_only_identifiers(self):
        import sqlite3
        from logbook.source_plan_broker import source_plan
        from logbook.plan_producer import request_job_ids
        with TemporaryDirectory() as temporary:
            db = Path(temporary)/'ledger.sqlite'
            with sqlite3.connect(db) as connection:
                connection.execute('CREATE TABLE recording_jobs (id INTEGER PRIMARY KEY, private TEXT)')
                connection.executemany('INSERT INTO recording_jobs VALUES (?,?)',[(1,'secret'),(2,'secret'),(4,'secret')])
            config = SimpleNamespace(sqlite_path=db)
            source = source_plan(config,b'{"after":1,"limit":2}')
            self.assertEqual(json.loads(source),{'job_ids':[2,4]})
            self.assertEqual(json.loads(produce_request(config,Path('/unused'),b'{"after":1,"limit":2}')),
                             {'job_ids':[2,4]})
            with patch('logbook.plan_producer._request_source_ids',return_value=[2,4]):
                response = produce_request(None,Path('/unused'),b'{"after":1,"limit":2}')
            self.assertNotIn(b'secret',response)
            with patch('logbook.plan_producer.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=response)):
                self.assertEqual(request_job_ids(1,2),[2,4])
            for raw in (b'{"after":true,"limit":2}',b'{"after":0,"limit":101}',b'{"after":0,"limit":1,"extra":1}'):
                with self.assertRaises(ValueError):
                    source_plan(config,raw)

    def test_job_discovery_rejects_invalid_requests_and_responses(self):
        from logbook.plan_producer import _request_source_ids, parse_index_request, request_job_ids, validate_job_ids
        for raw in (b'bad',b'x'*1025,b'{}',b'{"after":-1,"limit":1}',b'{"after":0,"limit":0}'):
            with self.assertRaises(ValueError):
                parse_index_request(raw)
        for raw in (b'bad',b'x'*5001,b'{}',b'{"job_ids":[1,1]}',b'{"job_ids":[true]}',b'{"job_ids":[2,1]}'):
            with self.assertRaises(ValueError):
                validate_job_ids(raw,0,2)
        with patch('logbook.plan_producer.subprocess.run',return_value=SimpleNamespace(returncode=1,stdout=b'')):
            with self.assertRaises(RuntimeError):
                _request_source_ids(0,2)
            with self.assertRaises(RuntimeError):
                request_job_ids(0,2)
        with patch('logbook.plan_producer.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=b'{"job_ids":[1,2]}')):
            self.assertEqual(_request_source_ids(0,2),[1,2])

    def test_source_handoff_validates_response_and_fixed_command(self):
        from logbook.plan_producer import _request_source_plan

        plan = {'nodes':[{'id':'logbook:job:000001'}], 'relationships':[], 'generated_at':'now'}
        result = SimpleNamespace(returncode=0, stdout=json.dumps(plan).encode())
        with patch('logbook.plan_producer.subprocess.run', return_value=result) as invoke:
            self.assertEqual(_request_source_plan(1), plan)
            self.assertEqual(invoke.call_args.args[0], ['/usr/bin/sudo', '-n', '-u', 'bernd',
                                                       '/usr/local/libexec/logbook-plan-producer'])
            self.assertEqual(json.loads(invoke.call_args.kwargs['input']), {'job_id':1})
        for bad in (b'bad', b'{}', json.dumps({**plan, 'nodes':[]}).encode(), b'x'*5000001):
            with self.subTest(size=len(bad)), patch('logbook.plan_producer.subprocess.run',
                                                   return_value=SimpleNamespace(returncode=0, stdout=bad)):
                with self.assertRaises((ValueError, RuntimeError)):
                    _request_source_plan(1)
        with patch('logbook.plan_producer.subprocess.run', return_value=SimpleNamespace(returncode=1, stdout=b'')):
            with self.assertRaises(RuntimeError):
                _request_source_plan(1)

    def test_source_configuration_must_be_root_controlled(self):
        import stat
        from logbook.source_plan_broker import trusted_config

        path = Path('/protected/source.env')
        def good(current):
            mode = stat.S_IFREG if current == path else stat.S_IFDIR
            return SimpleNamespace(st_uid=0, st_mode=mode|0o755)
        with patch.object(Path, 'lstat', good):
            trusted_config(path)
        for mode, uid in [(stat.S_IFREG|0o666, 0), (stat.S_IFLNK|0o777, 0), (stat.S_IFREG|0o644, 501)]:
            with self.subTest(mode=mode, uid=uid), patch.object(Path, 'lstat', return_value=SimpleNamespace(st_uid=uid, st_mode=mode)):
                with self.assertRaisesRegex(ValueError, 'root controlled'):
                    trusted_config(path)

    def test_source_handoff_signs_real_ledger_plan_without_caller_plan(self):
        from tests.test_memory_graph import _seed_memory_fixture
        from logbook.source_plan_broker import source_plan

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = _seed_memory_fixture(root)
            key = root/'producer'
            subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
            allowed = root/'allowed'
            allowed.write_text('producer '+key.with_suffix('.pub').read_text())
            allowed.chmod(0o600)
            source = source_plan(config, b'{"job_id":1}')
            with patch('logbook.plan_producer._request_source_plan', return_value=json.loads(source)):
                response = json.loads(produce_request(None, key, b'{"job_id":1}'))
            verify_plan(response['plan'], 1, response['proof'], allowed)
            self.assertTrue(any(n['id']=='logbook:job:000001' for n in response['plan']['nodes']))
            for raw in (b'{}', b'{"job_id":true}', b'{"job_id":1,"plan":{}}', b'x'*1025):
                with self.subTest(raw=raw[:30]), self.assertRaises(ValueError):
                    source_plan(config, raw)

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
            produce_request(object(), Path('/unused'), b'{"job_id":1}')

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
