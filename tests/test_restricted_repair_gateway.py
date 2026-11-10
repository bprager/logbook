from unittest import TestCase

from logbook.restricted_repair_gateway import permitted_command


class RestrictedRepairGatewayTests(TestCase):
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
