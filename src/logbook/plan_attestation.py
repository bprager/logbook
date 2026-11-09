"""Candidate automated producer proof; requires separately protected producer and inputs."""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path

from logbook.graph_maintenance_worker import read_private_json
from logbook.named_graph_approval import _private_bytes, plan_digest
from logbook.operator_plan import prepare_job_plan


def _encoded(statement: dict) -> bytes:
    return json.dumps(statement, sort_keys=True, separators=(',', ':')).encode()


def issue_plan(config: object, job_id: int, signing_key: Path, *, now: int | None = None) -> tuple[dict, dict]:
    """Generate the plan from the configured ledger; never sign caller-supplied plans."""
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        plan_path = root/'plan.json'
        prepare_job_plan(config, job_id, plan_path)
        plan = read_private_json(plan_path)
    return plan, sign_generated_plan(plan, job_id, signing_key, now=now)


def sign_generated_plan(plan: dict, job_id: int, signing_key: Path, *, now: int | None = None) -> dict:
    _private_bytes(signing_key)
    current = int(time.time()) if now is None else now
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        statement = {'schema': 1, 'job_id': job_id, 'plan_sha256': plan_digest(plan),
                     'issued_at': current, 'expires_at': current + 300}
        statement_path = root/'statement.json'
        statement_path.write_bytes(_encoded(statement))
        statement_path.chmod(0o600)
        subprocess.run(['/usr/bin/ssh-keygen', '-Y', 'sign', '-n', 'logbook-repair-plan',
                        '-f', str(signing_key), str(statement_path)], check=True,
                       capture_output=True, timeout=10)
        signature = (root/'statement.json.sig').read_text()
    return {'statement': statement, 'signature': signature}


def verify_plan(plan: dict, job_id: int, proof: dict, allowed: Path, *, now: int | None = None) -> None:
    """Validate producer signature and bind it to the requested job and exact plan."""
    current = int(time.time()) if now is None else now
    if (not isinstance(proof, dict) or set(proof) != {'statement', 'signature'}
            or not isinstance(proof['statement'], dict) or not isinstance(proof['signature'], str)
            or len(proof['signature']) > 10000):
        raise ValueError('invalid plan proof')
    statement = proof['statement']
    if (set(statement) != {'schema', 'job_id', 'plan_sha256', 'issued_at', 'expires_at'}
            or type(statement['schema']) is not int or statement['schema'] != 1
            or type(job_id) is not int or job_id < 1 or statement['job_id'] != job_id
            or type(statement['job_id']) is not int or statement['plan_sha256'] != plan_digest(plan)
            or type(statement['issued_at']) is not int or type(statement['expires_at']) is not int
            or statement['expires_at'] - statement['issued_at'] != 300
            or not statement['issued_at'] <= current <= statement['expires_at']):
        raise ValueError('plan proof does not match request')
    _private_bytes(allowed)
    with tempfile.TemporaryDirectory() as temporary:
        signature = Path(temporary)/'proof.sig'
        signature.write_text(proof['signature'])
        signature.chmod(0o600)
        result = subprocess.run(['/usr/bin/ssh-keygen', '-Y', 'verify', '-f', str(allowed),
                                 '-I', 'producer', '-n', 'logbook-repair-plan', '-s', str(signature)],
                                input=_encoded(statement), capture_output=True, timeout=10)
    if result.returncode:
        raise ValueError('plan producer signature invalid')
