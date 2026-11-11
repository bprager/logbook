"""Shared bounds for owner-approved graph plans and individual write batches."""
from __future__ import annotations

import time

MAX_BATCH_RECORDS = 500
MAX_PLAN_RECORDS = 5000


def policy_plan_limit(policy: dict, *, now: int | None = None) -> int:
    """Validate policy shape without changing the meaning of legacy signatures."""
    if not isinstance(policy, dict) or type(policy.get('schema')) is not int:
        raise ValueError('invalid or expired standing policy')
    schema = policy['schema']
    fields = {'schema', 'expires_at'}
    if schema in (1, 2):
        fields |= {'max_records'}
        if schema == 1:
            fields |= {'revision', 'bundle_sha256'}
        limit = policy.get('max_records')
        valid_bounds = type(limit) is int and 1 <= limit <= MAX_BATCH_RECORDS
    elif schema == 3:
        fields |= {'max_batch_records', 'max_plan_records'}
        limit = policy.get('max_plan_records')
        batch = policy.get('max_batch_records')
        valid_bounds = (type(batch) is int and batch == MAX_BATCH_RECORDS
                        and type(limit) is int and 1 <= limit <= MAX_PLAN_RECORDS)
    else:
        raise ValueError('invalid or expired standing policy')
    expiry = policy.get('expires_at')
    current = int(time.time()) if now is None else now
    if (set(policy) != fields or not valid_bounds
            or not ((schema in (2, 3) and expiry is None)
                    or (type(expiry) is int and expiry >= current))):
        raise ValueError('invalid or expired standing policy')
    return limit
