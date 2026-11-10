import json
import subprocess
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch


class RepairDiscoveryClientTests(TestCase):
    def test_fixed_restricted_discovery_validates_response(self):
        from logbook.repair_discovery_client import discover_missing_jobs
        response = {'container_id':'a'*64,'pid':42,'missing_job_ids':[2]}
        with patch('logbook.repair_discovery_client.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(response))) as run:
            self.assertEqual(discover_missing_jobs([1,2]),response)
            self.assertEqual(run.call_args.args[0][-2:],['logbook-repair@odin','sudo -n /usr/local/libexec/logbook-repair-dispatcher named-discover'])
        for bad in ({**response,'missing_job_ids':[3]},{**response,'pid':0}, {'unapproved':'x'}):
            with patch('logbook.repair_discovery_client.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(bad))):
                with self.assertRaises(ValueError):
                    discover_missing_jobs([1,2])
        with patch('logbook.repair_discovery_client.subprocess.run',return_value=SimpleNamespace(returncode=1,stdout='')):
            with self.assertRaises(ConnectionError):
                discover_missing_jobs([1,2])
        with patch('logbook.repair_discovery_client.subprocess.run',side_effect=subprocess.TimeoutExpired('ssh',60)):
            with self.assertRaises(TimeoutError):
                discover_missing_jobs([1,2])
        for raw in ('x'*5001,'{'):
            with patch('logbook.repair_discovery_client.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=raw)):
                with self.assertRaises(ValueError):
                    discover_missing_jobs([1,2])
