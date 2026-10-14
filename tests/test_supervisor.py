import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from logbook.supervisor import RecoveryState


class RecoveryTests(TestCase):
    def test_three_retries_then_latched_alarm_survives_restart(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            state = RecoveryState(path)
            restart = Mock()
            for now in (100, 160, 220, 280, 1000):
                state.observe('receiver', False, now, restart)
                state.save(now)
                state = RecoveryState(path)
            self.assertEqual(restart.call_count, 3)
            self.assertTrue(state.services['receiver']['exhausted'])
            self.assertEqual(state.services['receiver']['attempts'], 3)
            self.assertEqual(json.loads(path.read_text())['checked_at'], 1000)

    def test_healthy_window_resets_budget_but_brief_flap_does_not(self):
        with TemporaryDirectory() as tmp:
            state = RecoveryState(Path(tmp) / 'state.json')
            restart = Mock()
            state.observe('api', False, 100, restart)
            state.observe('api', True, 160, restart)
            state.observe('api', True, 500, restart)
            state.observe('api', False, 550, restart)
            self.assertEqual(restart.call_count, 2)
            state.observe('api', True, 600, restart)
            state.observe('api', True, 1200, restart)
            self.assertEqual(state.services['api']['attempts'], 0)
            state.observe('api', False, 1260, restart)
            self.assertEqual(restart.call_count, 3)

    def test_failed_restart_counts_and_throttle_is_enforced(self):
        with TemporaryDirectory() as tmp:
            state = RecoveryState(Path(tmp) / 'state.json')
            restart = Mock(side_effect=OSError('secret path'))
            state.observe('worker', False, 100, restart)
            state.observe('worker', False, 101, restart)
            self.assertEqual(restart.call_count, 1)
            self.assertEqual(state.services['worker']['detail'], 'restart_failed')
            self.assertNotIn('secret', str(state.services))

    def test_read_only_snapshot_metrics_staleness_and_redaction(self):
        from logbook.supervisor import read_supervision, supervision_metrics, supervision_summary
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(read_supervision(root)['state'], 'unavailable')
            folder = root / 'services'
            folder.mkdir()
            state = RecoveryState(folder / 'supervisor-state.json')
            state.observe('receiver', False, 100, Mock())
            state.services['receiver']['secret'] = 'never expose'
            state.save(100)
            self.assertEqual(read_supervision(root, now=150)['state'], 'ok')
            self.assertEqual(read_supervision(root, now=500)['state'], 'stale')
            self.assertNotIn('never expose', str(read_supervision(root)))
            self.assertEqual(len(supervision_metrics(root)), 4)
            self.assertIn('receiver down (1/3)', supervision_summary(read_supervision(root)))
            state.services['receiver']['exhausted'] = True
            state.save(100)
            self.assertIn('ALARM', supervision_summary(read_supervision(root)))
            self.assertIn('not configured', supervision_summary(None))
            state.path.write_text('{')
            self.assertEqual(read_supervision(root)['state'], 'unavailable')
            with self.assertRaises(ValueError):
                RecoveryState(state.path)

    def test_child_process_success_failure_schedule_and_timeout(self):
        import sys
        from logbook.supervisor import ManagedService
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = {'name': 'queue', 'command': [sys.executable, '-c', 'pass'], 'interval': 60}
            service = ManagedService(spec, root)
            self.assertFalse(service.healthy(100))
            service.start(100)
            service.process.wait(5)
            self.assertTrue(service.healthy(110))
            self.assertFalse(service.scheduled(120))
            self.assertTrue(service.scheduled(170))
            service.spec['command'] = [sys.executable, '-c', 'raise SystemExit(1)']
            service.start(200)
            service.process.wait(5)
            self.assertFalse(service.healthy(210))
            self.assertFalse(service.scheduled(300))
            service.spec['command'] = [sys.executable, '-c', 'import time; time.sleep(60)']
            service.start(300)
            self.assertTrue(service.healthy(350))
            self.assertFalse(service.healthy(2200))
            child = service.process
            service.stop()
            self.assertIsNotNone(child.poll())

    def test_health_probe_grace_and_remote_restart(self):
        import httpx
        from unittest.mock import patch
        from logbook.supervisor import ManagedService
        with TemporaryDirectory() as tmp:
            service = ManagedService({'name': 'odin', 'restart_command': ['restart'],
                                      'health_url': 'http://test/health', 'health_field': 'healthy',
                                      'health_value': True}, Path(tmp))
            with patch('logbook.supervisor.subprocess.run') as restart:
                service.start(100)
                restart.assert_called_once()
            with patch('httpx.get', side_effect=httpx.ConnectError('offline')):
                self.assertIsNone(service.healthy(120))
                self.assertFalse(service.healthy(200))
            reply = Mock()
            reply.json.return_value = {'healthy': True}
            with patch('httpx.get', return_value=reply):
                self.assertTrue(service.healthy(200))
                reply.json.return_value = {'healthy': False}
                self.assertFalse(service.healthy(201))

    def test_run_loop_persists_reset_and_shutdown(self):
        from threading import Event
        from unittest.mock import patch
        from logbook.supervisor import run, atomic_json
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            stop = Event()
            config = {'root': tmp, 'services': [{'name': 'queue', 'command': ['unused'], 'interval': 60}]}
            fake = Mock()
            fake.spec = config['services'][0]
            fake.healthy.return_value = True
            fake.scheduled.return_value = True
            with patch('logbook.supervisor.ManagedService', return_value=fake), \
                    patch.object(stop, 'wait', side_effect=lambda n: stop.set()):
                run(config, stop)
            self.assertEqual(fake.start.call_count, 2)
            fake.stop.assert_called_once()
            state = json.loads((root / 'supervisor-state.json').read_text())
            self.assertTrue(state['services']['queue']['healthy'])
            atomic_json(root / 'reset-request.json', {'service': 'queue'})
            stop.clear()
            fake.healthy.return_value = False
            with patch('logbook.supervisor.ManagedService', return_value=fake), \
                    patch.object(stop, 'wait', side_effect=lambda n: stop.set()):
                run(config, stop)
            self.assertFalse((root / 'reset-request.json').exists())
            self.assertEqual(json.loads((root / 'supervisor-state.json').read_text())['services']['queue']['attempts'], 1)

    def test_metric_file_queue_age_and_manual_reset(self):
        import sqlite3
        from unittest.mock import patch
        from logbook.supervisor import main, write_metrics, queue_pending_age
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / 'services'
            root.mkdir()
            ledger = Path(tmp) / 'ledger.sqlite'
            from logbook.ledger import open_ledger
            opened = open_ledger(ledger, initialize=True)
            opened.close()
            with sqlite3.connect(ledger) as db:
                db.execute('''INSERT INTO recording_jobs(checksum_sha256,source_device,source_filename,
                    source_path,size_bytes,modified_at,status,first_seen_at,last_seen_at)
                    VALUES('hash','remote','a.mp3','/unused',1,'2026-10-11T12:00:00+00:00',
                    'copied','2026-10-11T12:00:00+00:00','2026-10-11T12:00:00+00:00')''')
                db.execute('INSERT INTO remote_delivery(job_id) VALUES(1)')
            self.assertAlmostEqual(queue_pending_age(ledger, now=1791723600), 3600, delta=1)
            config = {'root': str(root), 'ledger_path': str(ledger)}
            settings = root / 'config.json'
            settings.write_text(json.dumps(config))
            state = RecoveryState(root / 'supervisor-state.json')
            state.observe('queue', True, 100, Mock())
            state.save(100)
            write_metrics(config)
            self.assertIn('logbook_service_healthy{service="queue"} 1', (root / 'metrics/logbook.prom').read_text())
            main(['--config', str(settings), '--reset', 'queue'])
            self.assertEqual(json.loads((root / 'reset-request.json').read_text())['service'], 'queue')
            with patch('logbook.supervisor.run') as run, patch('logbook.supervisor.signal.signal') as signals:
                main(['--config', str(settings)])
                self.assertEqual(signals.call_count, 2)
                signals.call_args.args[1]()
                self.assertTrue(run.call_args.args[1].is_set())
            with sqlite3.connect(ledger) as db:
                db.execute("UPDATE recording_jobs SET vault_synced_at='done'")
            self.assertEqual(queue_pending_age(ledger), 0)
            self.assertEqual(queue_pending_age(Path(tmp) / 'missing'), -1)

    def test_stop_escalates_hung_child_and_log_rotation(self):
        import subprocess
        from unittest.mock import patch
        from logbook.supervisor import ManagedService
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = ManagedService({'name': 'api', 'command': ['unused'], 'health_url': 'http://test'}, root)
            service.process = Mock()
            service.process.poll.return_value = None
            service.process.wait.side_effect = [subprocess.TimeoutExpired('test', 10), 0]
            child = service.process
            service.stop()
            child.kill.assert_called_once()
            (root / 'api.log').write_bytes(b'x' * (5 * 1024**2 + 1))
            with patch('logbook.supervisor.subprocess.Popen') as process:
                service.start(100)
                process.return_value.poll.return_value = 1
                self.assertFalse(service.healthy(101))
            self.assertTrue((root / 'api.previous.log').exists())

    def test_run_rejects_unknown_service_and_exports_metrics(self):
        from threading import Event
        from unittest.mock import patch
        from logbook.supervisor import run
        with TemporaryDirectory() as tmp:
            stop = Event()
            with self.assertRaises(ValueError):
                run({'root': tmp, 'services': [{'name': 'unapproved'}]}, stop)
            spec = {'name': 'receiver', 'command': ['unused']}
            fake = Mock(spec= ['spec','start','stop','healthy','scheduled'])
            fake.spec = spec
            fake.healthy.return_value = True
            fake.scheduled.return_value = False
            with patch('logbook.supervisor.ManagedService', return_value=fake), \
                    patch('logbook.supervisor.write_metrics') as metrics, \
                    patch.object(stop, 'wait', side_effect=lambda n: stop.set()):
                run({'root': tmp, 'services': [spec], 'ledger_path': '/unused'}, stop)
                metrics.assert_called_once()


    def test_startup_grace_does_not_claim_readiness_or_spend_retry(self):
        with TemporaryDirectory() as tmp:
            state = RecoveryState(Path(tmp) / 'state.json')
            restart = Mock()
            state.observe('receiver', None, 100, restart)
            restart.assert_not_called()
            self.assertIsNone(state.services['receiver']['healthy'])
            self.assertEqual(state.services['receiver']['detail'], 'starting')

    def test_supervisor_restart_preserves_budget_and_failed_start_is_bounded(self):
        from threading import Event
        from unittest.mock import patch
        from logbook.supervisor import run
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = RecoveryState(root / 'supervisor-state.json')
            state.observe('api', False, 100, Mock())
            state.observe('api', True, 200, Mock())
            state.save(200)
            stop = Event()
            spec = {'name': 'api', 'command': ['unused']}
            fake = Mock()
            fake.spec = spec
            fake.healthy.return_value = True
            fake.scheduled.return_value = False
            with patch('logbook.supervisor.ManagedService', return_value=fake), \
                    patch.object(stop, 'wait', side_effect=lambda n: stop.set()):
                run({'root': tmp, 'services': [spec]}, stop)
            self.assertEqual(json.loads(state.path.read_text())['services']['api']['attempts'], 1)
            fake.start.side_effect = OSError('failed')
            fake.healthy.return_value = False
            stop.clear()
            with patch('logbook.supervisor.ManagedService', return_value=fake), \
                    patch.object(stop, 'wait', side_effect=lambda n: stop.set()):
                run({'root': tmp, 'services': [spec]}, stop)
            self.assertEqual(json.loads(state.path.read_text())['services']['api']['attempts'], 2)
