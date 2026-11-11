from unittest import TestCase
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from logbook.restricted_repair_gateway import permitted_command


class RestrictedRepairGatewayTests(TestCase):
    def test_existing_source_holder_requests_fit_recovery_profile(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from logbook.lock_guard_broker import FixedRecovery
        from tests.test_named_repair_dispatcher import NamedRepairDispatcherTests
        request = NamedRepairDispatcherTests().request()
        del request['plan_data'], request['plan_proof']
        remote = FixedRecovery(request)
        for action in ('named-status', 'named-outcome', 'named-cancel'):
            with patch('logbook.lock_guard_broker.subprocess.run',
                       return_value=SimpleNamespace(returncode=0, stdout='fixture\n')) as run:
                remote._invoke(action)
            self.assertEqual(permitted_command(run.call_args.args[0][-1], recovery_only=True),
                             ['/usr/bin/sudo', '-n', '/usr/local/libexec/logbook-repair-dispatcher', action])

    def test_recovery_key_allows_only_status_outcome_and_safe_cancellation(self):
        prefix = 'sudo -n /usr/local/libexec/logbook-repair-dispatcher '
        for action in ('named-status', 'named-outcome', 'named-cancel'):
            self.assertEqual(permitted_command(prefix + action, recovery_only=True),
                             ['/usr/bin/sudo', '-n', '/usr/local/libexec/logbook-repair-dispatcher', action])
        for action in ('named-launch', 'named-stage', 'named-check', 'named-preflight',
                       'named-authorize', 'named-offline', 'named-discover', 'named-plan-discover'):
            with self.subTest(action=action), self.assertRaises(ValueError):
                permitted_command(prefix + action, recovery_only=True)
        token = 'a' * 32
        commands = ['sudo -n /usr/local/libexec/logbook-repair-release-receiver',
                    'sudo -n /usr/local/libexec/logbook-repair-bundle-installer '
                    f'/var/lib/logbook-graph-guard/bundles/bundle-{token} '
                    f'/var/lib/logbook-graph-guard/bundles/window-{token}',
                    prefix + 'named-status extra', prefix + 'named-status; id', 'id']
        for command in commands:
            with self.subTest(command=command), self.assertRaises(ValueError):
                permitted_command(command, recovery_only=True)

    def test_standalone_recovery_entry_rejects_bad_profiles_before_execution(self):
        source = Path(__file__).resolve().parents[1] / 'src/logbook/restricted_repair_gateway.py'
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            sudo = root/'sudo'
            sudo.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            sudo.chmod(0o700)
            gateway = root/'gateway.py'
            gateway.write_text(source.read_text().replace('/usr/bin/sudo', str(sudo)))
            env = os.environ | {'SSH_ORIGINAL_COMMAND':
                                'sudo -n /usr/local/libexec/logbook-repair-dispatcher named-status'}
            allowed = subprocess.run([sys.executable, '-I', str(gateway), '--recovery'],
                                     env=env, capture_output=True, text=True)
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            self.assertEqual(allowed.stdout.splitlines(),
                             ['-n', '/usr/local/libexec/logbook-repair-dispatcher', 'named-status'])
            for arguments in (['--unknown'], ['--recovery', 'extra']):
                result = subprocess.run([sys.executable, '-I', str(gateway), *arguments],
                                        env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 64)
                self.assertEqual(result.stdout, '')
            env['SSH_ORIGINAL_COMMAND'] = 'sudo -n /usr/local/libexec/logbook-repair-dispatcher named-launch'
            rejected = subprocess.run([sys.executable, '-I', str(gateway), '--recovery'],
                                      env=env, capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertEqual(rejected.stdout, '')

    def test_accepts_only_existing_named_client_commands(self):
        token = 'a' * 32
        base = '/var/lib/logbook-graph-guard/bundles/'
        installer = ('sudo -n /usr/local/libexec/logbook-repair-bundle-installer '
                     + base + 'bundle-' + token + ' ' + base + 'window-' + token)
        self.assertEqual(permitted_command(installer), [
            '/usr/bin/sudo', '-n', '/usr/local/libexec/logbook-repair-bundle-installer',
            base + 'bundle-' + token, base + 'window-' + token,
        ])
        self.assertEqual(permitted_command('sudo -n /usr/local/libexec/logbook-repair-dispatcher named-status'),
                         ['/usr/bin/sudo', '-n', '/usr/local/libexec/logbook-repair-dispatcher', 'named-status'])
        self.assertEqual(permitted_command('sudo -n /usr/local/libexec/logbook-repair-dispatcher named-cancel'),
                         ['/usr/bin/sudo', '-n', '/usr/local/libexec/logbook-repair-dispatcher', 'named-cancel'])
        self.assertEqual(permitted_command('sudo -n /usr/local/libexec/logbook-repair-dispatcher named-check'),
                         ['/usr/bin/sudo', '-n', '/usr/local/libexec/logbook-repair-dispatcher', 'named-check'])
        self.assertEqual(permitted_command('sudo -n /usr/local/libexec/logbook-repair-dispatcher named-plan-discover'),
                         ['/usr/bin/sudo', '-n', '/usr/local/libexec/logbook-repair-dispatcher', 'named-plan-discover'])
        self.assertEqual(permitted_command('sudo -n /usr/local/libexec/logbook-repair-dispatcher named-discover'),
                         ['/usr/bin/sudo', '-n', '/usr/local/libexec/logbook-repair-dispatcher', 'named-discover'])
        self.assertEqual(permitted_command('sudo -n /usr/local/libexec/logbook-repair-release-receiver'),
                         ['/usr/bin/sudo', '-n', '/usr/local/libexec/logbook-repair-release-receiver'])

    def test_rejects_shell_and_unapproved_or_mismatched_targets(self):
        base = '/var/lib/logbook-graph-guard/bundles/'
        bad = [
            '', 'sudo -n /bin/sh', '"unterminated', 'x' * 1025,
            'sudo -n /usr/local/libexec/logbook-repair-dispatcher named-status; touch /tmp/unsafe',
            'sudo -n /usr/local/libexec/logbook-repair-dispatcher named-unknown',
            'sudo -n /usr/local/libexec/logbook-repair-dispatcher named-status extra',
            'sudo -n /usr/local/libexec/logbook-repair-release-receiver extra',
            'sudo -n /usr/local/libexec/logbook-repair-bundle-installer '
            + base + 'bundle-' + 'a'*32 + ' ' + base + 'window-' + 'b'*32,
            'sudo -n /usr/local/libexec/logbook-repair-bundle-installer /tmp/bundle '
            + base + 'window-' + 'a'*32,
        ]
        for command in bad:
            with self.subTest(command=command), self.assertRaises(ValueError):
                permitted_command(command)
