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
