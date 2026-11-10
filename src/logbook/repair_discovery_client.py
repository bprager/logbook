"""Ask the restricted graph gateway for missing job identifiers only."""
from __future__ import annotations

import json
import re
import subprocess

from logbook.graph_repair_discovery import parse_job_ids
from logbook.lock_guard_client import OPERATOR_KEY


def discover_missing_jobs(ids: list[int]) -> dict:
    ids = parse_job_ids({'job_ids':ids})
    try:
        result = subprocess.run(['ssh','-i',str(OPERATOR_KEY),'-o','BatchMode=yes','-o','ConnectTimeout=5',
                                 '-o','StrictHostKeyChecking=yes','logbook-repair@odin',
                                 'sudo -n /usr/local/libexec/logbook-repair-dispatcher named-discover'],
                                input=json.dumps({'job_ids':ids}),capture_output=True,text=True,timeout=60)
    except subprocess.TimeoutExpired as error:
        raise TimeoutError('graph discovery timed out') from error
    if result.returncode:
        raise ConnectionError('graph discovery unavailable')
    if len(result.stdout) > 5000:
        raise ValueError('graph discovery response too large')
    try:
        response = json.loads(result.stdout)
        if (not isinstance(response,dict) or set(response) != {'container_id','pid','missing_job_ids'}
                or not isinstance(response['container_id'],str) or not re.fullmatch(r'[a-f0-9]{64}',response['container_id'])
                or type(response['pid']) is not int or response['pid'] < 1
                or not isinstance(response['missing_job_ids'],list)
                or any(value not in ids for value in response['missing_job_ids'])):
            raise ValueError('invalid graph discovery response')
        if response['missing_job_ids']:
            parse_job_ids({'job_ids':response['missing_job_ids']})
        return response
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError('invalid graph discovery response') from error


def discover_partial(job_id: int) -> dict:
    """Compare one signed source plan; return only target identity and completeness."""
    from logbook.plan_producer import request_generated_plan
    if type(job_id) is not int or not 1 <= job_id <= 9223372036854775807:
        raise ValueError('positive job ID required')
    plan, proof = request_generated_plan(job_id)
    request = json.dumps({'job_id':job_id,'plan':plan,'proof':proof})
    if len(request.encode()) > 5000000:
        raise ValueError('plan discovery request too large')
    try:
        result = subprocess.run(['ssh','-i',str(OPERATOR_KEY),'-o','BatchMode=yes','-o','ConnectTimeout=5',
                                 '-o','StrictHostKeyChecking=yes','logbook-repair@odin',
                                 'sudo -n /usr/local/libexec/logbook-repair-dispatcher named-plan-discover'],
                                input=request,capture_output=True,text=True,timeout=60)
    except subprocess.TimeoutExpired as error:
        raise TimeoutError('plan discovery timed out') from error
    if result.returncode or len(result.stdout) > 1000:
        raise ConnectionError('plan discovery unavailable')
    try:
        response = json.loads(result.stdout)
        if (not isinstance(response,dict) or set(response) != {'container_id','pid','incomplete'}
                or not isinstance(response['container_id'],str) or not re.fullmatch(r'[a-f0-9]{64}',response['container_id'])
                or type(response['pid']) is not int or response['pid'] < 1 or type(response['incomplete']) is not bool):
            raise ValueError('invalid plan discovery response')
        return response
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError('invalid plan discovery response') from error
