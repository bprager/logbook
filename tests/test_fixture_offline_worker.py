import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from logbook.fixture_offline_worker import rollback


class FixtureOfflineWorkerTests(unittest.TestCase):
    def test_reports_rollback_and_already_absent(self):
        driver = MagicMock()
        driver.__enter__.return_value = driver
        with patch('logbook.fixture_offline_worker.read_private_json', return_value={'batch':'fixture'}), \
             patch('logbook.fixture_offline_worker.GraphDatabase.driver', return_value=driver), \
             patch('logbook.fixture_offline_worker.GraphWriteLock'), \
             patch('logbook.fixture_offline_worker.rollback_one_batch', side_effect=[None,'absent']):
            self.assertEqual(rollback(Path('/journal'), Path('/private'), 7697), 'rolled-back')
            self.assertEqual(rollback(Path('/journal'), Path('/private'), 7697), 'not-retained')

    def test_connection_retry_and_failure(self):
        from neo4j.exceptions import ServiceUnavailable
        driver = MagicMock()
        driver.__enter__.return_value = driver
        with patch('logbook.fixture_offline_worker.read_private_json', return_value={'batch':'fixture'}), \
             patch('logbook.fixture_offline_worker.GraphDatabase.driver', return_value=driver), \
             patch('logbook.fixture_offline_worker.GraphWriteLock'), \
             patch('logbook.fixture_offline_worker.rollback_one_batch', return_value=None), \
             patch('logbook.fixture_offline_worker.time.sleep'):
            driver.verify_connectivity.side_effect = [ServiceUnavailable('offline'), None]
            self.assertEqual(rollback(Path('/journal'), Path('/private'), 7697), 'rolled-back')
            driver.verify_connectivity.side_effect = ServiceUnavailable('offline')
            with self.assertRaises(ServiceUnavailable):
                rollback(Path('/journal'), Path('/private'), 7697)
