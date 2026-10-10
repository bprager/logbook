import contextlib
import io
import runpy
import sys
from unittest import TestCase
from unittest.mock import patch

from logbook.remote_cli import main


class RemoteCliTests(TestCase):
    def test_commands(self):
        with patch('logbook.remote_cli.load_remote_config') as load, contextlib.redirect_stdout(io.StringIO()):
            with patch('logbook.remote_agent.run_agent', return_value={'pending_bytes': 0}) as run:
                self.assertEqual(main(['agent-run', '--config', 'sample.json']), 0)
                run.assert_called_once_with(load.return_value)
            with patch('uvicorn.run') as run, patch('logbook.remote_api.create_ingest_app') as app:
                self.assertEqual(main(['serve', '--config', 'sample.json']), 0)
                self.assertEqual(run.call_args.args, (app.return_value,))
                self.assertFalse(run.call_args.kwargs['proxy_headers'])
            with patch('logbook.remote_launchd.write_server_package', return_value=['server']):
                self.assertEqual(main(['package-server', '--config', 'sample.json', '--env', '.env',
                                       '--output', 'out', '--repo', '.']), 0)
            with patch('logbook.remote_launchd.write_agent_package', return_value=['package']) as package:
                self.assertEqual(main(['package-agent', '--config', 'sample.json', '--output', 'out',
                                       '--repo', '.', '--signing-identity', 'Local']), 0)
                package.assert_called_once()
            with patch.object(sys, 'argv', ['remote', 'agent-run', '--config', 'sample.json']), \
                    patch('logbook.remote_config.load_remote_config'), \
                    patch('logbook.remote_agent.run_agent', return_value={}), self.assertRaises(SystemExit) as exit:
                # Avoid runpy's already-imported-module warning.
                with patch.dict(sys.modules):
                    sys.modules.pop('logbook.remote_cli', None)
                    runpy.run_module('logbook.remote_cli', run_name='__main__')
            self.assertEqual(exit.exception.code, 0)
