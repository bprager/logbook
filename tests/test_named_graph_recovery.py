import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from logbook.fixture_offline_recovery import record_named_graph_storage, recover_named_graph_offline
from logbook.fixture_recovery_release import verify_named_graph_receipt, authorize_named_graph_restart


class NamedGraphRecoveryTests(TestCase):
    def test_records_only_the_named_graph_target_and_storage(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = {'private_dir':str(root), 'boot_guard_service':'memgraph.service',
                       'boot_guard_dir':str(root), 'journal':str(root/'journal'),
                       'unit':'test.service', 'port':7697, 'recovery_file':'/bundle/logbook/recovery.py'}
            recovery = Mock(container_id='a'*64, pid=12)
            mount = {'Type':'volume', 'Name':'memgraph-data-v313-20261011',
                     'Destination':'/var/lib/memgraph', 'RW':True}
            recovery.run.side_effect = [json.dumps([mount]), 'sha256:'+'b'*64]
            with patch('logbook.fixture_offline_recovery.NAMED_GUARD_DIR', root), \
                 patch('logbook.fixture_offline_recovery.verify_memgraph_binding') as binding, \
                 patch('logbook.fixture_offline_recovery.boot_id', return_value='old'):
                record_named_graph_storage(request, recovery)
                binding.assert_called_once_with(recovery)
            self.assertEqual(json.loads((root/'boot-target.json').read_text())['volume'], mount['Name'])
            request['boot_guard_service']='other.service'
            with patch('logbook.fixture_offline_recovery.NAMED_GUARD_DIR', root), self.assertRaises(ValueError):
                record_named_graph_storage(request, recovery)

    def test_named_recovery_rejects_unverified_receipt(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            request={'private_dir':str(root),'boot_guard_service':'memgraph.service',
                     'boot_guard_dir':str(root),'journal':str(root/'journal'),'unit':'unit',
                     'container_id':'a'*64,'pid':12,'port':7697,'recovery_file':'/bundle/logbook/recovery.py'}
            expected={'container':request['container_id'],'pid':12,'unit':'unit','service':'memgraph.service',
                      'guard_dir':str(root),'journal':request['journal'],'port':7697,'recovery_file':request['recovery_file']}
            batch='00000000-0000-0000-0000-000000000001'
            for name,value in [('boot-target.json',{**expected,'volume':'memgraph-data-v313-20261011',
                                                   'image':'sha256:'+'b'*64,'boot_id':'old'}),
                               ('journal',{'schema':1,'batch':batch})]:
                path=root/name
                path.write_text(json.dumps(value))
                path.chmod(0o600)
            with patch('logbook.fixture_offline_recovery.NAMED_GUARD_DIR',root), \
                 patch('logbook.fixture_offline_recovery.boot_id',return_value='new'), \
                 patch('logbook.fixture_offline_recovery.MemgraphBootGuard') as guard:
                guard.return_value.marker=root/'start-allowed'
                recovery=Mock()
                recovery.run.side_effect=['','container-id','321 true none',json.dumps([{
                    'Destination':'/var/lib/memgraph','Type':'volume','Name':'memgraph-data-v313-20261011','RW':True}]),
                    'rolled-back','','']
                self.assertEqual(recover_named_graph_offline(request,recovery),'rolled-back')
                command = recovery.run.call_args_list[1].args[0]
                self.assertIn('--storage-wal-enabled=true',command)
                self.assertIn('--storage-wal-file-flush-every-n-tx=1',command)
                self.assertIn('--storage-snapshot-interval-sec=300',command)
                self.assertIn('--storage-snapshot-on-exit=false',command)
            with patch('logbook.fixture_recovery_release.NAMED_GUARD_DIR',root), \
                 patch('logbook.fixture_recovery_release.boot_id',return_value='new'):
                self.assertEqual(verify_named_graph_receipt(request)['outcome'],'rolled-back')
                (root/'offline-outcome.json').unlink()
                with self.assertRaises(FileNotFoundError):
                    authorize_named_graph_restart(request,Mock())

    def test_named_restart_verifies_binding_image_volume_and_health(self):
        from logbook.fixture_recovery_release import fixture_restarted
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            request={'private_dir':str(root),'boot_guard_service':'memgraph.service',
                     'boot_guard_dir':str(root),'journal':str(root/'journal'),'unit':'unit',
                     'container_id':'a'*64,'pid':12,'port':7697,'recovery_file':'/bundle/logbook/recovery.py'}
            expected={'container':request['container_id'],'pid':12,'unit':'unit','service':'memgraph.service',
                      'guard_dir':str(root),'journal':request['journal'],'port':7697,'recovery_file':request['recovery_file']}
            batch='00000000-0000-0000-0000-000000000001'
            volume='memgraph-data-v313-20261011'
            image='sha256:'+'b'*64
            for name,value in [('boot-target.json',{**expected,'volume':volume,'image':image,'boot_id':'old'}),
                               ('offline-outcome.json',{**expected,'volume':volume,'boot_id':'new','outcome':'rolled-back','batch':batch}),
                               ('journal',{'schema':1,'batch':batch})]:
                path=root/name
                path.write_text(json.dumps(value))
                path.chmod(0o600)
            graph_id='c'*64
            recovery=Mock()
            recovery.run.side_effect=[f'{graph_id} 123 true {image}',json.dumps([{'Destination':'/var/lib/memgraph',
                'Type':'volume','Name':volume,'RW':True}]),graph_id,'"1"\n"1"\n']
            with patch('logbook.fixture_recovery_release.NAMED_GUARD_DIR',root), \
                 patch('logbook.fixture_recovery_release.boot_id',return_value='new'), \
                 patch('logbook.fixture_recovery_release.MemgraphBootGuard'), \
                 patch('logbook.fixture_recovery_release.verify_memgraph_binding') as binding:
                self.assertTrue(fixture_restarted(request,recovery,_named=True))
                binding.assert_called_once()
                self.assertEqual(recovery.run.call_args_list[0].args[0][-1],'memgraph-service')

    def test_named_restart_requires_matching_receipt_and_unused_volume(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            request={'boot_guard_service':'memgraph.service','boot_guard_dir':str(root),'port':7697}
            with patch('logbook.fixture_recovery_release.NAMED_GUARD_DIR',root), \
                 patch('logbook.fixture_recovery_release.verify_offline_receipt',return_value={'volume':'memgraph-data-v313-20261011'}), \
                 patch('logbook.fixture_recovery_release.MemgraphBootGuard') as guard, \
                 patch('logbook.fixture_recovery_release.fixture_restarted',return_value=True) as restarted:
                recovery=Mock()
                recovery.run.side_effect=['inactive','','']
                authorize_named_graph_restart(request,recovery)
                guard.return_value.allow.assert_called_once_with('inactive')
                restarted.assert_called_once_with(request,recovery,_named=True)

    def test_reboot_before_journal_records_truthful_prestart_outcome(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = {'private_dir':str(root),'boot_guard_service':'memgraph.service',
                       'boot_guard_dir':str(root),'journal':str(root/'journal'),'unit':'unit',
                       'container_id':'a'*64,'pid':12,'port':7697,'recovery_file':'/bundle/logbook/recovery.py'}
            expected = {'container':request['container_id'],'pid':12,'unit':'unit','service':'memgraph.service',
                        'guard_dir':str(root),'journal':request['journal'],'port':7697,'recovery_file':request['recovery_file']}
            target = root/'boot-target.json'
            target.write_text(json.dumps({**expected,'volume':'memgraph-data-v313-20261011',
                                          'image':'sha256:'+'b'*64,'boot_id':'old'}))
            target.chmod(0o600)
            recovery = Mock()
            recovery.run.return_value = ''
            with patch('logbook.fixture_offline_recovery.NAMED_GUARD_DIR',root), \
                 patch('logbook.fixture_offline_recovery.boot_id',return_value='new'), \
                 patch('logbook.fixture_offline_recovery.MemgraphBootGuard') as guard:
                guard.return_value.marker=root/'start-allowed'
                self.assertEqual(recover_named_graph_offline(request,recovery),'not-retained')
                self.assertFalse((root/'journal').exists())
                receipt=json.loads((root/'offline-outcome.json').read_text())
                self.assertEqual(receipt['phase'],'before-journal')
                self.assertIsNone(receipt['batch'])
            with patch('logbook.fixture_recovery_release.NAMED_GUARD_DIR',root), \
                 patch('logbook.fixture_recovery_release.boot_id',return_value='new'):
                self.assertEqual(verify_named_graph_receipt(request)['outcome'],'not-retained')
                (root/'journal').touch()
                with self.assertRaises(ValueError):
                    verify_named_graph_receipt(request)

    def test_prejournal_recovery_refuses_completion_or_busy_volume(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            request={'private_dir':str(root),'boot_guard_service':'memgraph.service',
                     'boot_guard_dir':str(root),'journal':str(root/'journal'),'unit':'unit',
                     'container_id':'a'*64,'pid':12,'port':7697,'recovery_file':'/bundle/logbook/recovery.py'}
            record={'container':request['container_id'],'pid':12,'unit':'unit','service':'memgraph.service',
                    'guard_dir':str(root),'journal':request['journal'],'port':7697,'recovery_file':request['recovery_file'],
                    'volume':'memgraph-data-v313-20261011','image':'sha256:'+'b'*64,'boot_id':'old'}
            (root/'boot-target.json').write_text(json.dumps(record))
            (root/'boot-target.json').chmod(0o600)
            with patch('logbook.fixture_offline_recovery.NAMED_GUARD_DIR',root), \
                 patch('logbook.fixture_offline_recovery.boot_id',return_value='new'), \
                 patch('logbook.fixture_offline_recovery.MemgraphBootGuard') as guard:
                guard.return_value.marker=root/'start-allowed'
                (root/'journal.complete').touch()
                with self.assertRaises(ValueError):
                    recover_named_graph_offline(request,Mock())
                (root/'journal.complete').unlink()
                recovery=Mock()
                recovery.run.return_value='busy'
                with self.assertRaisesRegex(ValueError,'in use'):
                    recover_named_graph_offline(request,recovery)
                (root/'boot-target.json').write_text(json.dumps({**record, 'volume':'wrong'}))
                with self.assertRaisesRegex(ValueError,'storage'):
                    recover_named_graph_offline(request,Mock())
                (root/'boot-target.json').write_text(json.dumps(record))
                def changed_during_check(_command):
                    (root/'journal').touch()
                    return ''
                recovery.run.side_effect=changed_during_check
                with self.assertRaisesRegex(ValueError,'appeared'):
                    recover_named_graph_offline(request,recovery)
            self.assertFalse((root/'offline-outcome.json').exists())
