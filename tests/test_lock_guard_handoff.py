import io
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch


class LockGuardHandoffTests(TestCase):
    def test_restricted_release_requires_matching_confirmed_recovery(self):
        from logbook.lock_guard_broker import hold_window
        from logbook.processing_restart_guard import arm_guard, require_clear_guard
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = SimpleNamespace(processing_root=root)
            unit = 'logbook-graph-maintenance-' + 'a' * 32 + '.service'
            arm_guard(root, unit)
            remote = Mock(request={'unit':unit})
            remote.recovered.return_value = False
            payload = io.StringIO(json.dumps({'release':{'unit':unit}}) + '\n')
            with self.assertRaisesRegex(RuntimeError, 'unconfirmed'):
                hold_window(config, lambda request: remote, payload, io.StringIO())
            with self.assertRaises(RuntimeError):
                require_clear_guard(root)
            with self.assertRaises(RuntimeError):
                hold_window(config, lambda request: remote, io.StringIO('{}\n'), io.StringIO())
            remote.recovered.return_value = True
            other = Mock(request={'unit':'logbook-graph-maintenance-' + 'b' * 32 + '.service'})
            other.recovered.return_value = True
            with self.assertRaises(ValueError):
                hold_window(config, lambda request: other,
                            io.StringIO(json.dumps({'release':{'unit':other.request['unit']}}) + '\n'), io.StringIO())
            with self.assertRaises(RuntimeError):
                require_clear_guard(root)
            output = io.StringIO()
            hold_window(config, lambda request: remote,
                        io.StringIO(json.dumps({'release':{'unit':unit}}) + '\n'), output)
            self.assertEqual(output.getvalue(), 'LOCKED\nRELEASED\n')
            remote.outcome.assert_called_once()
            require_clear_guard(root)

    def test_restricted_release_uses_fixed_holder(self):
        from logbook.lock_guard_client import release_named_handoff, OPERATOR_KEY, COMMAND
        from logbook.graph_maintenance_cross_host import NamedGraphMaintenance
        remote = Mock(spec=NamedGraphMaintenance, request={'unit':'fixture'})
        remote.host, remote.identity = 'logbook-repair@odin', OPERATOR_KEY
        result = SimpleNamespace(returncode=0, stdout='LOCKED\nRELEASED\n')
        with patch('logbook.lock_guard_client.subprocess.run', return_value=result) as run:
            release_named_handoff(remote)
            self.assertEqual(run.call_args.args[0], COMMAND)
            self.assertEqual(json.loads(run.call_args.kwargs['input']), {'release':remote.request})
        for result in (SimpleNamespace(returncode=1, stdout=''),
                       SimpleNamespace(returncode=0, stdout='LOCKED\n')):
            with patch('logbook.lock_guard_client.subprocess.run', return_value=result), \
                 self.assertRaisesRegex(RuntimeError, 'unconfirmed'):
                release_named_handoff(remote)

    def test_disconnected_client_leaves_real_holder_running(self):
        import fcntl
        from logbook.processing_restart_guard import require_clear_guard

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = '''
import os, sys
from pathlib import Path
from types import SimpleNamespace
from logbook.lock_guard_broker import hold_window
root = Path(os.environ['FIXTURE_ROOT'])
class Remote:
    def __init__(self, request): self.request = request
    def recovered(self): return (root / 'recovered').exists()
    def outcome(self): return 'retained'
hold_window(SimpleNamespace(processing_root=root), Remote, sys.stdin, sys.stdout)
'''
            child = subprocess.Popen([sys.executable, '-c', script],
                                     env={**os.environ, 'FIXTURE_ROOT':str(root)},
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            assert child.stdin and child.stdout
            try:
                self.assertEqual(child.stdout.readline(), 'LOCKED\n')
                unit = 'logbook-graph-maintenance-' + 'd' * 32 + '.service'
                child.stdin.write(json.dumps({'unit':unit}) + '\n')
                child.stdin.flush()
                self.assertEqual(child.stdout.readline(), 'ARMED\n')
                child.stdin.close()
                with (root / 'pipeline.lock').open('a') as contender:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    with self.assertRaises(RuntimeError):
                        require_clear_guard(root)
                    (root / 'recovered').touch()
                    self.assertEqual(child.wait(timeout=10), 0)
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                require_clear_guard(root)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()
                child.stdout.close()

    def test_recovery_queries_use_fixed_host_key_and_reject_changed_target(self):
        from logbook.lock_guard_broker import FixedRecovery
        from tests.test_named_repair_dispatcher import NamedRepairDispatcherTests

        request = NamedRepairDispatcherTests().request()
        del request['plan_data'], request['plan_proof']
        remote = FixedRecovery(request)
        response = SimpleNamespace(returncode=0, stdout='RECOVERED\n')
        with patch('logbook.lock_guard_broker.subprocess.run', return_value=response) as call:
            self.assertTrue(remote.recovered())
        argv = call.call_args.args[0]
        self.assertIn('logbook-repair@odin.local', argv)
        self.assertIn('HostKeyAlias=odin', argv)
        self.assertEqual(argv[1:3], ['-F', '/dev/null'])
        with self.assertRaises(ValueError):
            FixedRecovery({**request, 'journal':'/tmp/other'})
        with self.assertRaises(ValueError):
            remote._invoke('named-launch')
        with patch('logbook.lock_guard_broker.subprocess.run', return_value=SimpleNamespace(returncode=1)):
            with self.assertRaises(ConnectionError):
                remote.recovered()
        with patch.object(remote, '_invoke', return_value='unknown'):
            with self.assertRaises(RuntimeError):
                remote.outcome()
        with patch.object(remote, '_invoke', return_value='retained'):
            self.assertEqual(remote.outcome(), 'retained')

    def test_holder_rejects_bad_or_reused_windows_and_retries_errors(self):
        from logbook.lock_guard_broker import hold_window
        with TemporaryDirectory() as tmp:
            config = SimpleNamespace(processing_root=Path(tmp))
            for raw, remote in [('', Mock()), ('{}\n', Mock(request={'unit':'bad'})),
                                ('{}\n', Mock(request={'unit':'logbook-graph-maintenance-'+'a'*32+'.service'},
                                              recovered=Mock(return_value=True)))]:
                with self.subTest(raw=raw, remote=remote), self.assertRaises(ValueError):
                    hold_window(config, lambda request: remote, io.StringIO(raw), io.StringIO())
            remote = Mock(request={'unit':'logbook-graph-maintenance-'+'b'*32+'.service'})
            remote.recovered.side_effect = [False, ConnectionError(), True]
            with patch('logbook.lock_guard_broker.time.sleep'):
                hold_window(config, lambda request: remote, io.StringIO('{}\n'), io.StringIO())
            remote.outcome.assert_called_once()

    def test_client_fails_closed_on_invalid_connection_and_holder_failures(self):
        from logbook.lock_guard_client import coordinate_named_handoff, OPERATOR_KEY
        from logbook.graph_maintenance_cross_host import NamedGraphMaintenance
        remote = Mock(spec=NamedGraphMaintenance, request={})
        remote.host, remote.identity = 'wrong', OPERATOR_KEY
        with self.assertRaises(ValueError):
            coordinate_named_handoff(remote, lambda: {}, timeout_seconds=1)
        remote.host = 'logbook-repair@odin'
        for response, request, launch_error, wait_result, expected in [
                ('BAD\n', {}, None, 0, RuntimeError),
                ('LOCKED\n', {'large':'x'*9000}, None, 0, ValueError),
                ('LOCKED\nARMED\n', {}, None, 1, RuntimeError),
                ('LOCKED\nARMED\n', {}, ConnectionError(), 0, ConnectionError),
                ('LOCKED\nARMED\n', {}, None, subprocess.TimeoutExpired('holder', 1), TimeoutError)]:
            remote.request = request
            remote.launch.side_effect = launch_error
            child = Mock(stdin=io.StringIO(), stdout=io.StringIO(response))
            if isinstance(wait_result, Exception):
                child.wait.side_effect = wait_result
            else:
                child.wait.return_value = wait_result
            with self.subTest(expected=expected), patch('logbook.lock_guard_client.subprocess.Popen', return_value=child), \
                 patch('logbook.lock_guard_client.select.select', return_value=([child.stdout], [], [])), \
                 self.assertRaises(expected):
                coordinate_named_handoff(remote, lambda: {}, timeout_seconds=1)

    def test_client_refreshes_under_lock_and_launches_after_arm(self):
        from logbook.lock_guard_client import coordinate_named_handoff
        from logbook.graph_maintenance_cross_host import NamedGraphMaintenance

        request = {'unit':'logbook-graph-maintenance-' + 'c' * 32 + '.service'}
        remote = Mock(spec=NamedGraphMaintenance, request=request)
        remote.host = 'logbook-repair@odin'
        remote.identity = Path('/Library/Application Support/LogbookRepairOperator/private/odin-repair')
        events = []
        class Input(io.StringIO):
            def close(self):
                pass
        child = Mock(stdin=Input(), stdout=io.StringIO('LOCKED\nARMED\n'))
        child.wait.return_value = 0
        remote.stage.side_effect = lambda plan: events.append(('stage', plan))
        remote.preflight.side_effect = lambda: events.append('preflight')
        remote.launch.side_effect = lambda: events.append('launch')
        def refresh():
            events.append('refresh')
            return {'nodes':[]}
        with patch('logbook.lock_guard_client.subprocess.Popen', return_value=child), \
             patch('logbook.lock_guard_client.select.select', return_value=([child.stdout], [], [])):
            coordinate_named_handoff(remote, refresh, timeout_seconds=1)
        self.assertEqual(events, ['refresh', ('stage', {'nodes':[]}), 'preflight', 'launch'])
        self.assertEqual(json.loads(child.stdin.getvalue()), request)

    def test_holder_keeps_locks_until_fixed_remote_confirms_recovery(self):
        import fcntl
        from logbook.lock_guard_broker import hold_window
        from logbook.processing_restart_guard import require_clear_guard

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            unit = 'logbook-graph-maintenance-' + 'a' * 32 + '.service'
            remote = Mock(request={'unit': unit})
            remote.recovered.side_effect = [False, False, True]
            output = io.StringIO()

            def assert_locked(_seconds):
                with (root / 'pipeline.lock').open('a') as contender:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(RuntimeError):
                    require_clear_guard(root)

            with patch('logbook.lock_guard_broker.time.sleep', side_effect=assert_locked):
                hold_window(SimpleNamespace(processing_root=root), lambda request: remote,
                            io.StringIO(json.dumps({'unit': unit}) + '\n'), output)
            self.assertEqual(output.getvalue(), 'LOCKED\nARMED\n')
            remote.outcome.assert_called_once()
            require_clear_guard(root)

    def test_disconnected_operator_does_not_release_armed_holder(self):
        from logbook.lock_guard_broker import hold_window
        from logbook.processing_restart_guard import require_clear_guard

        class Disconnected(io.StringIO):
            def write(self, value):
                if value == 'ARMED\n':
                    raise BrokenPipeError()
                return super().write(value)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = Mock(request={'unit':'logbook-graph-maintenance-' + 'b' * 32 + '.service'})
            remote.recovered.side_effect = [False, True]
            hold_window(SimpleNamespace(processing_root=root), lambda request: remote,
                        io.StringIO('{}\n'), Disconnected())
            remote.outcome.assert_called_once()
            require_clear_guard(root)

    def test_invalid_request_never_arms_guard(self):
        from logbook.lock_guard_broker import hold_window
        from logbook.processing_restart_guard import require_clear_guard

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                hold_window(SimpleNamespace(processing_root=root), Mock(side_effect=ValueError),
                            io.StringIO('{}\n'), io.StringIO())
            require_clear_guard(root)
