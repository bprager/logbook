"""Fixed-configuration candidate for automatic repair plan production."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from logbook.config import load_app_config
from logbook.named_graph_approval import _private_bytes
from logbook.plan_attestation import issue_plan

CONFIG = Path('/Library/Application Support/LogbookPlanProducer/private/config.env')
KEY = Path('/Library/Application Support/LogbookPlanProducer/private/producer-key')
COMMAND = ['/usr/bin/sudo', '-n', '-u', 'logbook-plan-producer',
           '/usr/local/libexec/logbook-plan-producer']


def produce_request(config: object, key: Path, raw: bytes) -> bytes:
    if len(raw) > 1024:
        raise ValueError('producer request too large')
    try:
        request = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError('invalid producer request') from error
    if (not isinstance(request, dict) or set(request) != {'job_id'}
            or type(request['job_id']) is not int or request['job_id'] < 1):
        raise ValueError('producer requires one positive job ID')
    plan, proof = issue_plan(config, request['job_id'], key)
    response = json.dumps({'plan': plan, 'proof': proof}, sort_keys=True).encode()
    if len(response) > 5000000:
        raise ValueError('producer response too large')
    return response


def request_generated_plan(job_id: int) -> tuple[dict, dict]:
    if type(job_id) is not int or job_id < 1:
        raise ValueError('positive job ID required')
    result = subprocess.run(COMMAND, input=json.dumps({'job_id': job_id}).encode(),
                            capture_output=True, timeout=60, check=False)
    if result.returncode:
        raise RuntimeError('plan producer unavailable')
    if len(result.stdout) > 5000000:
        raise ValueError('producer response too large')
    try:
        response = json.loads(result.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError('invalid producer response') from error
    if (not isinstance(response, dict) or set(response) != {'plan', 'proof'}
            or not isinstance(response['plan'], dict) or not isinstance(response['proof'], dict)):
        raise ValueError('invalid producer response')
    return response['plan'], response['proof']


def main() -> None:  # pragma: no cover
    _private_bytes(CONFIG)
    config = load_app_config(CONFIG)
    sys.stdout.buffer.write(produce_request(config, KEY, sys.stdin.buffer.read(1025)))


if __name__ == '__main__':  # pragma: no cover
    main()
