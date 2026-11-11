import hashlib
import json
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from logbook.standing_graph_policy import authorize_repair


class StandingGraphPolicyTests(TestCase):
    def test_unknown_policy_shapes_never_expand_authority(self):
        from logbook.graph_repair_policy import policy_plan_limit
        for policy in (None, [], {}, {'schema': True}, {'schema': 4},
                       {'schema': 3, 'max_batch_records': 500,
                        'max_plan_records': True, 'expires_at': None}):
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                policy_plan_limit(policy)

    def test_large_source_plan_requires_explicit_new_policy(self):
        from logbook.standing_graph_policy import check_plan
        job = 'logbook:job:000147'
        plan = {'nodes': [
            {'id': job, 'labels': ['LogbookJob'], 'properties': {}},
            *[{'id': job + f':segment:{i:04d}', 'labels': ['TranscriptSegment'],
               'properties': {}} for i in range(600)],
        ], 'relationships': [], 'generated_at': 'fixture'}
        digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        release = {'revision': 'a' * 40, 'bundle_sha256': 'c' * 64}
        request = {**release, 'job_id': 147, 'plan_sha256': digest}
        legacy = {'schema': 2, 'max_records': 500, 'expires_at': None}
        with self.assertRaisesRegex(ValueError, 'exceeds policy'):
            check_plan(request, plan, legacy, release=release)
        policy = {'schema': 3, 'max_batch_records': 500, 'max_plan_records': 1000,
                  'expires_at': None}
        check_plan(request, plan, policy, release=release)
        link = {'id': job + ':has-segment:0000', 'type': 'HAS_SEGMENT',
                'start_id': job, 'end_id': job + ':segment:0000', 'properties': {}}
        for duplicate in ({**plan, 'nodes': plan['nodes'] + plan['nodes'][1:2]},
                          {**plan, 'relationships': [link, link]}):
            duplicate_digest = hashlib.sha256(json.dumps(
                duplicate, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            with self.assertRaisesRegex(ValueError, 'duplicate plan'):
                check_plan({**request, 'plan_sha256': duplicate_digest}, duplicate, policy, release=release)
        for changed in ({'max_plan_records': 600}, {'max_plan_records': 5001},
                        {'max_batch_records': 501}, {'max_batch_records': True},
                        {'expires_at': 99}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                check_plan(request, plan, {**policy, **changed}, release=release, now=100)

    def test_durable_policy_allows_releases_without_renewal_but_legacy_still_expires(self):
        from logbook.standing_graph_policy import check_plan
        plan = {'nodes':[{'id':'logbook:job:000147','labels':['LogbookJob'],'properties':{'project':'logbook','job_id':147}}],
                'relationships':[], 'generated_at':'now'}
        digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        release = {'revision':'a'*40, 'bundle_sha256':'c'*64}
        request = {**release, 'job_id':147, 'plan_sha256':digest}
        check_plan(request, plan, {'schema':2, 'max_records':5, 'expires_at':None}, now=9999999999, release=release)
        with self.assertRaises(ValueError):
            check_plan(request, plan, {'schema':1, **release, 'max_records':5, 'expires_at':None}, now=1)

    def test_signed_standing_policy_allows_matching_plan_without_per_run_signature(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.chmod(0o700)
            key = root / 'owner'
            subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
            (root / 'approval.allowed_signers').write_text('owner ' + key.with_suffix('.pub').read_text())
            (root / 'approval.allowed_signers').chmod(0o600)
            policy = {'schema': 1, 'revision': 'a' * 40, 'bundle_sha256': 'c' * 64, 'max_records': 5, 'expires_at': int(time.time()) + 600}
            path = root / 'standing-policy.json'
            path.write_text(json.dumps(policy))
            path.chmod(0o600)
            subprocess.run(['/usr/bin/ssh-keygen', '-Y', 'sign', '-n', 'logbook-graph-policy', '-f', str(key), str(path)], check=True, capture_output=True)
            (root / 'standing-policy.json.sig').chmod(0o600)
            plan = {'nodes': [{'id': 'logbook:job:000147', 'labels': ['LogbookJob'], 'properties': {'project': 'logbook', 'job_id': 147}}], 'relationships': [], 'generated_at': 'now'}
            digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            request = {'revision': 'a' * 40, 'bundle_sha256': 'c' * 64, 'job_id': 147, 'plan_sha256': digest}
            authorize_repair(request, plan, root)
            authorize_repair(request, plan, root)
            with self.assertRaises(ValueError):
                authorize_repair({**request, 'job_id': 148}, plan, root)
            with self.assertRaises(ValueError):
                authorize_repair({**request, 'revision': 'b' * 40}, plan, root)
            with self.assertRaises(ValueError):
                authorize_repair({**request, 'bundle_sha256': 'd' * 64}, plan, root)
            path.write_text(json.dumps({**policy, 'max_records': 500}))
            with self.assertRaises(ValueError):
                authorize_repair(request, plan, root)

    def test_rejects_expired_policy_and_changed_plan(self):
        from logbook.standing_graph_policy import check_plan
        plan = {'nodes': [{'id': 'logbook:job:000147', 'labels': ['LogbookJob'], 'properties': {'project': 'logbook', 'job_id': 147}}], 'relationships': [], 'generated_at': 'now'}
        digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        request = {'revision': 'a' * 40, 'bundle_sha256': 'c' * 64, 'job_id': 147, 'plan_sha256': digest}
        policy = {'schema': 1, 'revision': 'a' * 40, 'bundle_sha256': 'c' * 64, 'max_records': 5, 'expires_at': 100}
        with self.assertRaises(ValueError):
            check_plan(request, plan, policy, now=101)
        with self.assertRaises(ValueError):
            check_plan(request, {**plan, 'nodes': []}, {**policy, 'expires_at': 200}, now=101)

    def test_rejects_another_job_even_when_plan_digest_matches(self):
        from logbook.standing_graph_policy import check_plan
        plan = {'nodes': [{'id': 'logbook:job:000148', 'labels': ['LogbookJob'], 'properties': {}}], 'relationships': [], 'generated_at': 'now'}
        digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        request = {'revision': 'a' * 40, 'bundle_sha256': 'c' * 64, 'job_id': 147, 'plan_sha256': digest}
        policy = {'schema': 1, 'revision': 'a' * 40, 'bundle_sha256': 'c' * 64, 'max_records': 5, 'expires_at': 200}
        with self.assertRaisesRegex(ValueError, 'out-of-scope'):
            check_plan(request, plan, policy, now=101)

    def test_rejects_malformed_or_oversized_plan(self):
        from logbook.standing_graph_policy import check_plan
        policy = {'schema': 1, 'revision': 'a' * 40, 'bundle_sha256': 'c' * 64, 'max_records': 1, 'expires_at': 200}
        for plan in (
            {'nodes': [], 'relationships': [], 'generated_at': 'now'},
            {'nodes': [{}], 'relationships': [], 'generated_at': 'now'},
        ):
            digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            with self.assertRaises(ValueError):
                check_plan({'revision': 'a' * 40, 'bundle_sha256': 'c' * 64, 'job_id': 147, 'plan_sha256': digest}, plan, policy, now=101)

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
