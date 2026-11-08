import hashlib
import json
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from logbook.standing_graph_policy import authorize_repair


class StandingGraphPolicyTests(TestCase):
    def test_signed_standing_policy_allows_matching_plan_without_per_run_signature(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.chmod(0o700)
            key = root / 'owner'
            subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
            (root / 'approval.allowed_signers').write_text('owner ' + key.with_suffix('.pub').read_text())
            (root / 'approval.allowed_signers').chmod(0o600)
            policy = {'schema': 1, 'revision': 'a' * 40, 'max_records': 5, 'expires_at': int(time.time()) + 600}
            path = root / 'standing-policy.json'
            path.write_text(json.dumps(policy))
            path.chmod(0o600)
            subprocess.run(['/usr/bin/ssh-keygen', '-Y', 'sign', '-n', 'logbook-graph-policy', '-f', str(key), str(path)], check=True, capture_output=True)
            (root / 'standing-policy.json.sig').chmod(0o600)
            plan = {'nodes': [{'id': 'logbook:job:000147', 'labels': ['LogbookJob'], 'properties': {'project': 'logbook', 'job_id': 147}}], 'relationships': [], 'generated_at': 'now'}
            digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            request = {'revision': 'a' * 40, 'job_id': 147, 'plan_sha256': digest}
            authorize_repair(request, plan, root)
            authorize_repair(request, plan, root)
            with self.assertRaises(ValueError):
                authorize_repair({**request, 'job_id': 148}, plan, root)
            with self.assertRaises(ValueError):
                authorize_repair({**request, 'revision': 'b' * 40}, plan, root)
            path.write_text(json.dumps({**policy, 'max_records': 500}))
            with self.assertRaises(ValueError):
                authorize_repair(request, plan, root)

    def test_rejects_expired_policy_and_changed_plan(self):
        from logbook.standing_graph_policy import check_plan
        plan = {'nodes': [{'id': 'logbook:job:000147', 'labels': ['LogbookJob'], 'properties': {'project': 'logbook', 'job_id': 147}}], 'relationships': [], 'generated_at': 'now'}
        digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        request = {'revision': 'a' * 40, 'job_id': 147, 'plan_sha256': digest}
        policy = {'schema': 1, 'revision': 'a' * 40, 'max_records': 5, 'expires_at': 100}
        with self.assertRaises(ValueError):
            check_plan(request, plan, policy, now=101)
        with self.assertRaises(ValueError):
            check_plan(request, {**plan, 'nodes': []}, {**policy, 'expires_at': 200}, now=101)

    def test_rejects_another_job_even_when_plan_digest_matches(self):
        from logbook.standing_graph_policy import check_plan
        plan = {'nodes': [{'id': 'logbook:job:000148', 'labels': ['LogbookJob'], 'properties': {}}], 'relationships': [], 'generated_at': 'now'}
        digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        request = {'revision': 'a' * 40, 'job_id': 147, 'plan_sha256': digest}
        policy = {'schema': 1, 'revision': 'a' * 40, 'max_records': 5, 'expires_at': 200}
        with self.assertRaisesRegex(ValueError, 'out-of-scope'):
            check_plan(request, plan, policy, now=101)

    def test_rejects_malformed_or_oversized_plan(self):
        from logbook.standing_graph_policy import check_plan
        policy = {'schema': 1, 'revision': 'a' * 40, 'max_records': 1, 'expires_at': 200}
        for plan in (
            {'nodes': [], 'relationships': [], 'generated_at': 'now'},
            {'nodes': [{}], 'relationships': [], 'generated_at': 'now'},
        ):
            digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            with self.assertRaises(ValueError):
                check_plan({'revision': 'a' * 40, 'job_id': 147, 'plan_sha256': digest}, plan, policy, now=101)

    def test_rejects_signed_invalid_policy_content(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.chmod(0o700)
            key = root / 'owner'
            subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
            signers = root / 'approval.allowed_signers'
            signers.write_text('owner ' + key.with_suffix('.pub').read_text())
            signers.chmod(0o600)
            path = root / 'standing-policy.json'
            signature = root / 'standing-policy.json.sig'
            for content in ('[]', '{'):
                path.write_text(content)
                path.chmod(0o600)
                if signature.exists():
                    signature.unlink()
                subprocess.run(['/usr/bin/ssh-keygen', '-Y', 'sign', '-n', 'logbook-graph-policy', '-f', str(key), str(path)], check=True, capture_output=True)
                signature.chmod(0o600)
                with self.assertRaises(ValueError):
                    authorize_repair({}, {}, root)
