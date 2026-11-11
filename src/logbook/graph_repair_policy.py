"""Shared bounds, owned by the stdlib-only standalone bundle installer."""
from logbook.operator_bundle_bootstrap import (
    MAX_BATCH_RECORDS,
    MAX_PLAN_RECORDS,
    policy_plan_limit,
)

__all__ = ['MAX_BATCH_RECORDS', 'MAX_PLAN_RECORDS', 'policy_plan_limit']
