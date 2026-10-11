import plistlib
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless
from unittest.mock import patch

from logbook.remote_launchd import write_agent_package
from logbook.remote_config import AgentConfig
from logbook.launchd import _render_mount_runner_app, _write_app_bundle


class RemoteLaunchdTests(TestCase):
    def test_remote_compiler_uses_hardware_architecture(self):
        from logbook.remote_launchd import native_compiler
        with patch('logbook.remote_launchd.sys.platform', 'darwin'), \
                patch('logbook.remote_launchd.subprocess.run') as run:
            run.return_value = subprocess.CompletedProcess([], 0, '1\n', '')
            self.assertEqual(native_compiler(), ('/usr/bin/arch', '-arm64', '/usr/bin/cc'))
            run.assert_called_once_with(['/usr/sbin/sysctl', '-n', 'hw.optional.arm64'],
                                        capture_output=True, text=True, check=False, timeout=10)
            run.return_value = subprocess.CompletedProcess([], 0, '0\n', '')
            self.assertEqual(native_compiler(), ('/usr/bin/arch', '-x86_64', '/usr/bin/cc'))
            run.return_value = subprocess.CompletedProcess([], 1, '', 'unknown oid')
            self.assertEqual(native_compiler(), ('/usr/bin/arch', '-x86_64', '/usr/bin/cc'))
        with patch('logbook.remote_launchd.sys.platform', 'linux'):
            self.assertEqual(native_compiler(), ('cc',))

    def test_failed_remote_compile_preserves_installed_bundle(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = AgentConfig(root / 'buffer', root / 'SONY', 'SONY', 'uuid',
                                 'REC_FILE/FOLDER01', 'UTC', 'https://mimir', root / 'token')
            bundle = root / 'package' / 'LogbookRemoteAgent.app'
            info = bundle / 'Contents' / 'Info.plist'
            info.parent.mkdir(parents=True)
            info.write_bytes(b'previous signed bundle')
            with patch('logbook.remote_launchd.native_compiler', return_value=('cc',)), \
                    patch('logbook.launchd.subprocess.run', return_value=subprocess.CompletedProcess(
                        [], 1, '', "xcrun: libxcrun missing compatible architecture (need x86_64)")):
                with self.assertRaisesRegex(RuntimeError, 'missing compatible architecture'):
                    write_agent_package(config, root / 'config.json', root / 'package',
                                        Path.cwd(), 'Logbook Local Signing')
            self.assertEqual(info.read_bytes(), b'previous signed bundle')
            self.assertEqual(list((root / 'package').iterdir()), [bundle])

    def test_failed_signing_or_install_preserves_previous_bundle(self):
        for failure in ('sign', 'verify', 'replace', 'first_install'):
            with self.subTest(failure=failure), TemporaryDirectory() as tmp:
                root = Path(tmp)
                config = AgentConfig(root / 'buffer', root / 'SONY', 'SONY', 'uuid',
                                     'REC_FILE/FOLDER01', 'UTC', 'https://mimir', root / 'token')
                bundle = root / 'package' / 'LogbookRemoteAgent.app'
                if failure != 'first_install':
                    bundle.mkdir(parents=True)
                    (bundle / 'old').write_text('signed original')
                def build(app, **kwargs):
                    self.assertEqual(kwargs['compiler'], ('native-cc',))
                    app.bundle_path.mkdir(parents=True)
                def signing(command, **kwargs):
                    if ((failure == 'sign' and '--sign' in command) or
                            (failure == 'verify' and '--verify' in command)):
                        raise subprocess.CalledProcessError(1, command)
                    return subprocess.CompletedProcess(command, 0)
                original_rename = Path.rename
                def rename(path, destination):
                    if path.name == 'LogbookRemoteAgent.app' and path.parent.name.startswith('.remote-build-'):
                        raise OSError('simulated replacement failure')
                    return original_rename(path, destination)
                with patch('logbook.remote_launchd.native_compiler', return_value=('native-cc',)), \
                        patch('logbook.remote_launchd._write_app_bundle', side_effect=build), \
                        patch('logbook.remote_launchd.subprocess.run', side_effect=signing), \
                        patch.object(Path, 'rename', rename):
                    with self.assertRaises((OSError, subprocess.CalledProcessError)):
                        write_agent_package(config, root / 'config.json', root / 'package',
                                            Path.cwd(), 'Logbook Local Signing')
                if failure == 'first_install':
                    self.assertFalse(bundle.exists())
                else:
                    self.assertEqual((bundle / 'old').read_text(), 'signed original')

    def test_tunnel_is_loopback_only_and_restarts_without_prompts(self):
        from logbook.remote_launchd import write_tunnel_package
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token = root / 'token'
            token.write_text('x' * 40)
            token.chmod(0o600)
            config = AgentConfig(root / 'buffer', root / 'SONY', 'SONY', 'uuid',
                                 'REC_FILE/FOLDER01', 'UTC', 'http://127.0.0.1:18791', token,
                                 ssh_host='jump-mimir', ssh_remote_port=18791)
            path = write_tunnel_package(config, root / 'output')
            plist = plistlib.loads(path.read_bytes())
            self.assertTrue(plist['KeepAlive'])
            self.assertTrue(plist['RunAtLoad'])
            self.assertEqual(plist['ThrottleInterval'], 30)
            args = plist['ProgramArguments']
            self.assertEqual(args[:3], ['/usr/bin/ssh', '-N', '-T'])
            self.assertEqual(args[-3:], ['-L', '127.0.0.1:18791:127.0.0.1:18791', 'jump-mimir'])
            for option in ('BatchMode=yes', 'StrictHostKeyChecking=yes', 'ExitOnForwardFailure=yes',
                           'ConnectTimeout=15', 'ServerAliveInterval=15', 'ServerAliveCountMax=3',
                           'ForwardAgent=no', 'ForwardX11=no', 'ControlMaster=no', 'ControlPath=none'):
                self.assertIn(option, args)
            self.assertNotIn('x' * 40, path.read_text())
            ssh_config = Path(args[args.index('-F') + 1])
            contents = ssh_config.read_text()
            self.assertTrue(contents.startswith('Host *\n'))
            self.assertTrue(contents.endswith('Include ~/.ssh/config\n'))
            for setting in ('BatchMode yes', 'StrictHostKeyChecking yes',
                            'ForkAfterAuthentication no', 'ServerAliveInterval 15',
                            'ServerAliveCountMax 3', 'ControlPath none'):
                self.assertIn(setting, contents)
            if sys.platform == 'darwin':
                # No connection: inspect the implicit jump command emitted by native SSH.
                inspected = subprocess.run([*args[:-1], '-G', '-vv', '-J', 'test-jump', args[-1]],
                                           capture_output=True, text=True, check=True)
                proxy = next(line for line in inspected.stderr.splitlines()
                             if 'Setting implicit ProxyCommand' in line)
                self.assertIn('-F', proxy)
                self.assertIn(str(ssh_config), proxy)
                self.assertIn('test-jump', proxy)
                hop = subprocess.run(['/usr/bin/ssh', '-G', '-F', str(ssh_config), 'test-jump'],
                                     capture_output=True, text=True, check=True).stdout
                self.assertIn('batchmode yes', hop)
                self.assertIn('stricthostkeychecking true', hop)
                self.assertIn('forkafterauthentication no', hop)
            with self.assertRaises(ValueError):
                write_tunnel_package(replace(config, ssh_host=''), root / 'disabled')

    @skipUnless(sys.platform == 'darwin', 'requires macOS codesign')
    def test_generated_bundle_can_be_signed_and_repaired_in_place(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = _render_mount_runner_app(
                bundle_path=root / 'LogbookRemoteAgent.app',
                python_executable=sys.executable, repo_root=root, src_path=root,
                env_path=root / 'config.json', recorder_dir=root / 'SONY',
                module='logbook.remote_cli', command='agent-run', config_arg='--config',
                bundle_identifier='ws.prager.logbook.remote-agent')
            for repair in (False, True):
                if repair:
                    # Reproduce an old package left by the failed MacBook installation.
                    (app.executable_path.parent / 'LogbookMountRunner.c').write_text(app.source_content)
                _write_app_bundle(app)
                # Ad-hoc signing tests bundle structure only, without a keychain identity.
                # The installation API still requires a persistent signing certificate.
                for command in (
                    ['/usr/bin/codesign', '--force', '--sign', '-', str(app.bundle_path)],
                    ['/usr/bin/codesign', '--verify', '--strict', str(app.bundle_path)],
                ):
                    result = subprocess.run(command, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0,
                                     f'{command!r}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}')
                self.assertEqual([p.name for p in app.executable_path.parent.iterdir()],
                                 ['LogbookMountRunner'])

    def test_package_is_mount_login_and_retry_driven_with_no_cleanup(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = AgentConfig(root / 'buffer', root / 'SONY', 'SONY', 'uuid',
                                 'REC_FILE/FOLDER01', 'UTC', 'https://mimir', root / 'token')
            original_run = subprocess.run
            def execute(command, **kwargs):
                if command[0] == 'cc':
                    return original_run(command, **kwargs)
                if sys.platform == 'darwin':
                    command = [('-' if arg == 'Logbook Local Signing' else arg) for arg in command]
                    return original_run(command, **kwargs)
                return subprocess.CompletedProcess(command, 0)
            with patch('logbook.remote_launchd.subprocess.run', side_effect=execute) as run:
                paths = write_agent_package(config, root / 'config.json', root / 'package',
                                            Path.cwd(), 'Logbook Local Signing')
            plist = plistlib.loads(paths[0].read_bytes())
            self.assertTrue(plist['StartOnMount'])
            self.assertTrue(plist['RunAtLoad'])
            self.assertEqual(plist['StartInterval'], 60)
            self.assertNotIn('cleanup', paths[0].read_text())
            app = root / 'package' / 'LogbookRemoteAgent.app'
            info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
            self.assertEqual(info['CFBundleIdentifier'], 'ws.prager.logbook.remote-agent')
            self.assertTrue(info['LSUIElement'])
            source = (app / 'Contents/Resources/LogbookMountRunner.c').read_text()
            self.assertIn('logbook.remote_cli', source)
            self.assertIn('agent-run', source)
            self.assertNotIn('process-mounted-recorder', source)
            self.assertIn('codesign', str(run.call_args_list))
            with patch('logbook.remote_launchd.subprocess.run', side_effect=execute), \
                    patch('logbook.remote_launchd.write_tunnel_package', return_value=root / 'tunnel') as tunnel:
                configured = replace(config, ssh_host='jump-mimir')
                bundled = write_agent_package(configured, root / 'config.json', root / 'package',
                                               Path.cwd(), 'Logbook Local Signing')
                self.assertEqual(bundled[-1], root / 'tunnel')
                tunnel.assert_called_once_with(configured, (root / 'package').resolve())
            with self.assertRaises(ValueError):
                write_agent_package(config, root / 'config.json', root / 'package', Path.cwd(), '-')

    def test_server_package_uses_existing_ledger_pipeline_without_cleanup(self):
        from logbook.remote_launchd import write_server_package
        from logbook.remote_config import ServerConfig
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = ServerConfig(root / 'voice.sqlite', root / 'remote', root / 'devices')
            paths = write_server_package(config, root / 'remote.json', root / '.env',
                                         root / 'output', Path.cwd())
            api, worker = (plistlib.loads(path.read_bytes()) for path in paths)
            self.assertTrue(api['KeepAlive'])
            self.assertIn('serve', api['ProgramArguments'])
            self.assertIn('process-queued', worker['ProgramArguments'])
            self.assertEqual(worker['StartInterval'], 60)
            self.assertNotIn('cleanup', str(worker))
