import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch


class RestrictedBackupCommandTests(TestCase):
    def test_private_temporary_settings_preserve_other_values_and_are_removed(self):
        from logbook.restricted_backup import run
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = root/'source.env'
            env.write_text('SECRET=private\nLOGBOOK_BACKUP_ROOT=old\n')
            observed = {}
            def invoke(command, **kwargs):
                generated = Path(command[command.index('--env')+1])
                observed['path'] = generated
                observed['contents'] = generated.read_text()
                observed['mode'] = generated.stat().st_mode & 0o777
                observed['command'] = command
                return subprocess.CompletedProcess(command, 0)
            with patch('logbook.restricted_backup.subprocess.run', side_effect=invoke):
                self.assertEqual(run(['backup-run', '--env', str(env), '--repo-root', str(root), '--execute']), 0)
            self.assertEqual(observed['mode'], 0o600)
            self.assertFalse(observed['path'].exists())
            self.assertIn('SECRET=private\n', observed['contents'])
            self.assertIn('LOGBOOK_BACKUP_ROOT=192.168.1.3:/\n', observed['contents'])
            self.assertNotIn('LOGBOOK_BACKUP_ROOT=old', observed['contents'])
            self.assertIn('--execute', observed['command'])
            self.assertEqual(env.read_text(), 'SECRET=private\nLOGBOOK_BACKUP_ROOT=old\n')

    def test_invalid_command_options_are_rejected(self):
        from logbook.restricted_backup import run
        for args in (['backup-run', '--env', 'unused'],
                     ['backup-run', '--env', 'unused', '--repo-root', '/tmp', '--backup', 'one'],
                     ['backup-restore-drill', '--env', 'unused'],
                     ['backup-restore-drill', '--env', 'unused', '--backup', 'one', '--execute']):
            with self.subTest(args=args), self.assertRaises(SystemExit):
                run(args)

    def test_dry_run_propagates_failure(self):
        from logbook.restricted_backup import run
        with TemporaryDirectory() as tmp:
            env=Path(tmp)/'source.env'
            env.write_text('TEST=value\n')
            with patch('logbook.restricted_backup.subprocess.run', return_value=subprocess.CompletedProcess([], 2)) as call:
                self.assertEqual(run(['backup-run','--env',str(env),'--repo-root',tmp]),2)
            self.assertNotIn('--execute',call.call_args.args[0])

    def test_restore_requires_a_target_and_passes_it_on(self):
        from logbook.restricted_backup import run
        with TemporaryDirectory() as tmp:
            env=Path(tmp)/'source.env'
            env.write_text('TEST=value\n')
            with patch('logbook.restricted_backup.subprocess.run', return_value=subprocess.CompletedProcess([], 0)) as call:
                self.assertEqual(run(['backup-restore-drill','--env',str(env),'--backup','192.168.1.3:/one']),0)
            self.assertIn('192.168.1.3:/one',call.call_args.args[0])
