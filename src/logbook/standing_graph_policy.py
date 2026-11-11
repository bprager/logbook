"""Validate an owner-signed standing policy for bounded Logbook repair plans.

This candidate is not connected to production or an operator entry point.
The caller must independently protect the policy, verifier and trusted key.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from logbook.guarded_graph_repair import _is_canonical_node, _is_canonical_relationship
from logbook.memory_graph import MemoryNode, MemoryRelationship
from logbook.named_graph_approval import _private_bytes, plan_digest
from logbook.operator_bundle_bootstrap import approved_release
from logbook.graph_repair_policy import policy_plan_limit


def check_plan(request: dict, plan: dict, policy: dict, *, now: int | None = None,
               release: dict | None = None) -> None:
    limit = policy_plan_limit(policy, now=now)
    selected = policy if policy['schema'] == 1 else release
    if (not isinstance(selected, dict) or not isinstance(selected.get('revision'), str)
            or not re.fullmatch(r'[a-f0-9]{40}', selected['revision'])
            or not isinstance(selected.get('bundle_sha256'), str)
            or not re.fullmatch(r'[a-f0-9]{64}', selected['bundle_sha256'])):
        raise ValueError('invalid approved release')
    job = request.get('job_id')
    if (type(job) is not int or job < 1 or request.get('revision') != selected['revision']
            or request.get('bundle_sha256') != selected['bundle_sha256']
            or plan_digest(plan) != request.get('plan_sha256')):
        raise ValueError('repair request does not match policy or plan')
    if (set(plan) != {'nodes', 'relationships', 'generated_at'}
            or not isinstance(plan['nodes'], list) or not isinstance(plan['relationships'], list)
            or not isinstance(plan['generated_at'], str)
            or not 1 <= len(plan['nodes']) + len(plan['relationships']) <= limit):
        raise ValueError('repair plan exceeds policy')
    job_prefix = f'logbook:job:{job:06d}'
    shared = ('logbook:topic:', 'logbook:person:', 'logbook:project:')
    def in_scope(identifier: str) -> bool:
        return identifier == job_prefix or identifier.startswith(job_prefix + ':')
    try:
        nodes = [MemoryNode(n['id'], tuple(n['labels']), n['properties']) for n in plan['nodes']]
        relationships = [MemoryRelationship(**r) for r in plan['relationships']]
        if (len({node.id for node in nodes}) != len(nodes)
                or len({rel.id for rel in relationships}) != len(relationships)):
            raise ValueError('duplicate plan identifiers')
        if (any(not isinstance(n.id, str) or not isinstance(n.properties, dict)
                or not _is_canonical_node(n) or not (in_scope(n.id) or n.id.startswith(shared))
                for n in nodes)
                or any(not _is_canonical_relationship(r)
                       or not (in_scope(r.start_id) or in_scope(r.end_id))
                       or not all(in_scope(v) or v.startswith(shared) for v in (r.start_id, r.end_id))
                       for r in relationships)):
            raise ValueError('repair plan contains out-of-scope records')
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError('invalid repair plan') from error


def authorize_repair(request: dict, plan: dict, guard: Path, *, now: int | None = None) -> None:
    policy_path = guard / 'standing-policy.json'
    signature = guard / 'standing-policy.json.sig'
    allowed = guard / 'approval.allowed_signers'
    content = _private_bytes(policy_path)
    _private_bytes(signature)
    _private_bytes(allowed)
    result = subprocess.run(['/usr/bin/ssh-keygen', '-Y', 'verify', '-f', str(allowed),
                             '-I', 'owner', '-n', 'logbook-graph-policy', '-s', str(signature)],
                            input=content, capture_output=True, timeout=10)
    if result.returncode:
        raise ValueError('standing policy signature invalid')
    try:
        policy = json.loads(content)
        if not isinstance(policy, dict):
            raise ValueError('standing policy must be an object')
        release = None
        if policy.get('schema') in (2, 3):
            unit = request.get('unit')
            match = re.fullmatch(r'logbook-graph-maintenance-([a-f0-9]{32})\.service', unit) if isinstance(unit, str) else None
            if match is None:
                raise ValueError('invalid repair unit')
            release = approved_release(guard, guard/'bundles'/('bundle-'+match.group(1)))
        check_plan(request, plan, policy, now=now, release=release)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError('invalid standing policy') from error
