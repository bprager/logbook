import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from logbook.plan_attestation import issue_plan, verify_plan


class PlanAttestationTests(TestCase):
    def test_producer_signs_generated_plan_and_verifier_rejects_changes(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            key = root/'producer'
            subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
            allowed = root/'plan.allowed_signers'
            allowed.write_text('producer ' + key.with_suffix('.pub').read_text())
            allowed.chmod(0o600)
            plan = {'nodes': [{'id': 'logbook:job:000007', 'properties': {'text': 'source'}}],
                    'relationships': [], 'generated_at': 'now'}
            def prepare(config, job, path):
                self.assertEqual(job, 7)
                path.write_text(json.dumps(plan))
                path.chmod(0o600)
            with patch('logbook.plan_attestation.prepare_job_plan', side_effect=prepare):
                produced, proof = issue_plan(object(), 7, key, now=100)
            self.assertEqual(produced, plan)
            verify_plan(produced, 7, proof, allowed, now=101)
            with self.assertRaises(ValueError):
                verify_plan({**plan, 'nodes': []}, 7, proof, allowed, now=101)
            with self.assertRaises(ValueError):
                verify_plan(plan, 8, proof, allowed, now=101)
            with self.assertRaises(ValueError):
                verify_plan(plan, 7, proof, allowed, now=401)
            with self.assertRaises(ValueError):
                verify_plan(plan, 7, {**proof, 'signature': proof['signature'][:-10]}, allowed, now=101)

    def test_real_ledger_plan_can_be_generated_and_verified_automatically(self):
        from tests.test_memory_graph import _seed_memory_fixture
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = _seed_memory_fixture(root)
            key = root/'producer'
            subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
            allowed = root/'plan.allowed_signers'
            allowed.write_text('producer ' + key.with_suffix('.pub').read_text())
            allowed.chmod(0o600)
            plan, proof = issue_plan(config, 1, key, now=100)
            self.assertTrue(any(n['id'] == 'logbook:job:000001' for n in plan['nodes']))
            verify_plan(plan, 1, proof, allowed, now=101)

    def test_rejects_malformed_proofs_and_statement_fields(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            allowed = root/'allowed'
            allowed.write_text('producer ssh-ed25519 invalid')
            allowed.chmod(0o600)
            plan = {'nodes': [], 'relationships': [], 'generated_at': 'now'}
            for proof in (None, {}, {'statement': {}, 'signature': 'x'*10001},
                          {'statement': {}, 'signature': ''}):
                with self.subTest(proof_type=type(proof).__name__), self.assertRaises(ValueError):
                    verify_plan(plan, 1, proof, allowed, now=101)
