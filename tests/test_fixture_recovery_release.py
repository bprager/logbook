import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from logbook.fixture_recovery_release import verify_offline_receipt, authorize_fixture_restart


class FixtureRecoveryReleaseTests(unittest.TestCase):
    def state(self, root):
        request = {'private_dir':str(root),'boot_guard_service':'logbook-fixture-graph.service',
                   'boot_guard_dir':str(root),'journal':str(root/'journal'),'unit':'unit',
                   'container_id':'a'*64,'pid':12,'port':7697,'recovery_file':'/bundle/logbook/recovery.py'}
        expected = {'container':request['container_id'],'pid':12,'unit':'unit',
                    'service':request['boot_guard_service'],'guard_dir':str(root),'journal':request['journal'],
                    'port':7697,'recovery_file':request['recovery_file']}
        batch='00000000-0000-0000-0000-000000000001'
        for name,value in [('boot-target.json',{**expected,'volume':'logbook-fixture-data',
                                                'image':'sha256:'+'b'*64,'boot_id':'old'}),
                           ('offline-outcome.json',{**expected,'volume':'logbook-fixture-data',
                                                    'boot_id':'new','outcome':'rolled-back','batch':batch}),
                           ('journal',{'schema':1,'batch':batch})]:
            path=root/name
            path.write_text(json.dumps(value))
            path.chmod(0o600)
        return request

    def test_receipt_bound_to_original_target_and_batch(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            request=self.state(root)
            with patch('logbook.fixture_recovery_release.boot_id',return_value='new'):
                self.assertEqual(verify_offline_receipt(request)['outcome'],'rolled-back')
                receipt=root/'offline-outcome.json'
                value=json.loads(receipt.read_text())
                value['batch']='00000000-0000-0000-0000-000000000002'
                receipt.write_text(json.dumps(value))
                with self.assertRaisesRegex(ValueError,'receipt'):
                    verify_offline_receipt(request)

    def test_restart_requires_proof_and_unused_volume(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            request=self.state(root)
            recovery=Mock()
            recovery.run.side_effect=['inactive', '', '']
            with patch('logbook.fixture_recovery_release.boot_id',return_value='new'), \
                 patch('logbook.fixture_recovery_release.FixtureBootGuard') as guard:
                guard.return_value.marker=root/'start-allowed'
                with patch('logbook.fixture_recovery_release.fixture_restarted',return_value=True):
                    authorize_fixture_restart(request,recovery)
                guard.return_value.allow.assert_called_once_with('inactive')
                self.assertEqual(recovery.run.call_args_list[2].args[0],
                                 ['systemctl','start','logbook-fixture-graph.service'])

    def test_service_must_match_recorded_image_volume_and_be_healthy(self):
        from logbook.fixture_recovery_release import fixture_restarted
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            request=self.state(root)
            graph_id='c'*64
            volume=json.dumps([{'Type':'volume','Name':'logbook-fixture-data',
                                'Destination':'/var/lib/memgraph','RW':True}])
            for scenario in ('good','image','volume','consumer','health'):
                with self.subTest(scenario=scenario):
                    recovery=Mock()
                    image='sha256:' + ('d' if scenario == 'image' else 'b')*64
                    mounts='[]' if scenario == 'volume' else volume
                    consumer='other' if scenario == 'consumer' else graph_id
                    health='bad' if scenario == 'health' else '"1"\n"1"\n'
                    recovery.run.side_effect=[graph_id+' 123 true '+image,mounts,consumer,health]
                    with patch('logbook.fixture_recovery_release.boot_id',return_value='new'), \
                         patch('logbook.fixture_recovery_release.FixtureBootGuard'), \
                         patch('logbook.fixture_recovery_release.verify_fixture_binding'):
                        if scenario == 'good':
                            self.assertTrue(fixture_restarted(request,recovery))
                        else:
                            with self.assertRaises(ValueError):
                                fixture_restarted(request,recovery)

    def test_restart_refuses_busy_volume_and_invalid_receipt(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            request=self.state(root)
            recovery=Mock()
            recovery.run.side_effect=['inactive','other-container']
            with patch('logbook.fixture_recovery_release.boot_id',return_value='new'), \
                 patch('logbook.fixture_recovery_release.FixtureBootGuard') as guard:
                guard.return_value.marker=root/'start-allowed'
                with self.assertRaisesRegex(ValueError,'in use'):
                    authorize_fixture_restart(request,recovery)
                guard.return_value.allow.assert_not_called()
            receipt=root/'offline-outcome.json'
            data=json.loads(receipt.read_text())
            data['outcome']='unknown'
            receipt.write_text(json.dumps(data))
            with patch('logbook.fixture_recovery_release.boot_id',return_value='new'):
                with self.assertRaisesRegex(ValueError,'receipt'):
                    authorize_fixture_restart(request,Mock())

    def test_invalid_target_and_retry_health_check(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            request=self.state(root)
            target=root/'boot-target.json'
            data=json.loads(target.read_text())
            data['pid']=999
            target.write_text(json.dumps(data))
            with patch('logbook.fixture_recovery_release.boot_id',return_value='new'):
                with self.assertRaisesRegex(ValueError,'target'):
                    verify_offline_receipt(request)
            self.state(root)
            recovery=Mock()
            recovery.run.side_effect=['active']
            with patch('logbook.fixture_recovery_release.boot_id',return_value='new'), \
                 patch('logbook.fixture_recovery_release.FixtureBootGuard'), \
                 patch('logbook.fixture_recovery_release.fixture_restarted',return_value=True) as restarted:
                authorize_fixture_restart(request,recovery)
                restarted.assert_called_once()
            recovery.run.side_effect=['inactive','','']
            with patch('logbook.fixture_recovery_release.boot_id',return_value='new'), \
                 patch('logbook.fixture_recovery_release.FixtureBootGuard'), \
                 patch('logbook.fixture_recovery_release.time.sleep'), \
                 patch('logbook.fixture_recovery_release.fixture_restarted',side_effect=[ValueError('not ready'),True]):
                authorize_fixture_restart(request,recovery)
            recovery.run.side_effect=['inactive','','']
            with patch('logbook.fixture_recovery_release.boot_id',return_value='new'), \
                 patch('logbook.fixture_recovery_release.FixtureBootGuard'), \
                 patch('logbook.fixture_recovery_release.time.sleep'), \
                 patch('logbook.fixture_recovery_release.fixture_restarted',side_effect=ValueError('not ready')):
                with self.assertRaisesRegex(ValueError,'not ready'):
                    authorize_fixture_restart(request,recovery)
