import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch


class RepairCycleTests(TestCase):
    def test_one_missing_job_uses_existing_repair_path(self):
        from logbook.repair_cycle import repair_cycle
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root/(hashlib.sha256(b'fixture').hexdigest()+'.tar.gz')
            package.write_bytes(b'fixture')
            package.chmod(0o600)
            (root/'current.json').write_text(json.dumps({'schema':2,'sequence':1,'revision':'a'*40,
                'bundle_sha256':hashlib.sha256(b'fixture').hexdigest(),'bundle_path':str(package)}))
            (root/'current.json').chmod(0o600)
            with patch('logbook.repair_cycle.request_job_ids', return_value=[1,2,3]), \
                 patch('logbook.repair_cycle.discover_missing_jobs', return_value={
                     'container_id':'b'*64,'pid':42,'missing_job_ids':[2,3]}), \
                 patch('logbook.repair_cycle.prepare_named', return_value='prepared') as prepare, \
                 patch('logbook.repair_cycle.run_named', return_value='retained') as run:
                self.assertEqual(repair_cycle(root), 'retained')
                self.assertEqual(prepare.call_args.args[1],2)
                self.assertEqual(prepare.call_args.args[2]['container_id'],'b'*64)
                self.assertTrue(prepare.call_args.args[2]['protected_executor'])
                self.assertTrue(run.call_args.kwargs['restricted'])

    def test_existing_job_with_missing_details_is_repaired(self):
        from logbook.repair_cycle import repair_cycle
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            digest=hashlib.sha256(b'fixture').hexdigest()
            package=root/(digest+'.tar.gz')
            package.write_bytes(b'fixture')
            current=root/'current.json'
            current.write_text(json.dumps({'schema':2,'sequence':1,'revision':'a'*40,
                                          'bundle_sha256':digest,'bundle_path':str(package)}))
            current.chmod(0o600)
            with patch('logbook.repair_cycle.request_job_ids',return_value=[1,2]), \
                 patch('logbook.repair_cycle.discover_missing_jobs',return_value={
                     'container_id':'b'*64,'pid':42,'missing_job_ids':[]}), \
                 patch('logbook.repair_cycle.discover_partial',return_value={
                     'container_id':'c'*64,'pid':43,'incomplete':True}), \
                 patch('logbook.repair_cycle.prepare_named',return_value='prepared') as prepare, \
                 patch('logbook.repair_cycle.run_named',return_value='retained'):
                self.assertEqual(repair_cycle(root),'retained')
                self.assertEqual(prepare.call_args.args[1],1)
                self.assertEqual(prepare.call_args.args[2]['container_id'],'c'*64)

    def test_page_cursor_advances_only_after_empty_discovery(self):
        from logbook.repair_cycle import repair_cycle
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('logbook.repair_cycle.request_job_ids', side_effect=[list(range(1,101)),[],[1]]) as source, \
                 patch('logbook.repair_cycle.discover_missing_jobs', return_value={
                     'container_id':'b'*64,'pid':42,'missing_job_ids':[]}), \
                 patch('logbook.repair_cycle.discover_partial',return_value={'container_id':'b'*64,'pid':42,'incomplete':False}):
                self.assertEqual(repair_cycle(root), 'page-complete')
                self.assertEqual(repair_cycle(root), 'scan-complete')
                self.assertEqual(repair_cycle(root), 'scan-complete')
            self.assertEqual([call.args for call in source.call_args_list],[(0,100),(1,100),(0,100)])

    def test_rejects_modified_release_and_preserves_cursor(self):
        from logbook.repair_cycle import repair_cycle
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            digest = hashlib.sha256(b'expected').hexdigest()
            package = root/(digest+'.tar.gz')
            package.write_bytes(b'modified')
            (root/'current.json').write_text(json.dumps({'schema':2,'sequence':1,'revision':'a'*40,
                'bundle_sha256':digest,'bundle_path':str(package)}))
            (root/'current.json').chmod(0o600)
            with patch('logbook.repair_cycle.request_job_ids',return_value=[1]), \
                 patch('logbook.repair_cycle.discover_missing_jobs',return_value={
                     'container_id':'b'*64,'pid':42,'missing_job_ids':[1]}), \
                 patch('logbook.repair_cycle.prepare_named') as prepare:
                with self.assertRaisesRegex(ValueError,'package mismatch'):
                    repair_cycle(root)
                prepare.assert_not_called()
            self.assertFalse((root/'repair-cursor.json').exists())

    def test_invalid_cursor_release_and_lock_are_rejected(self):
        from logbook.repair_cycle import repair_cycle
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cursor = root/'repair-cursor.json'
            cursor.write_text('{"after":-1}')
            cursor.chmod(0o600)
            with self.assertRaisesRegex(ValueError,'invalid repair cursor'):
                repair_cycle(root)
            cursor.unlink()
            (root/'repair-cycle.lock').chmod(0o666)
            with self.assertRaisesRegex(ValueError,'private repair cycle lock'):
                repair_cycle(root)
            (root/'repair-cycle.lock').chmod(0o600)
            (root/'current.json').write_text('{}')
            (root/'current.json').chmod(0o600)
            with patch('logbook.repair_cycle.request_job_ids',return_value=[1]), \
                 patch('logbook.repair_cycle.discover_missing_jobs',return_value={
                     'container_id':'b'*64,'pid':42,'missing_job_ids':[1]}):
                with self.assertRaisesRegex(ValueError,'invalid current repair release'):
                    repair_cycle(root)

    def test_failed_cursor_write_removes_temporary_file(self):
        from logbook.repair_cycle import repair_cycle
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('logbook.repair_cycle.request_job_ids',return_value=[]), \
                 patch('logbook.repair_cycle.os.replace',side_effect=OSError('failed')):
                with self.assertRaises(OSError):
                    repair_cycle(root)
            self.assertFalse(list(root.glob('.repair-cursor.json-*')))

    def test_unsuccessful_preparation_and_outcome_stop_cycle(self):
        from logbook.repair_cycle import repair_cycle
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            digest = hashlib.sha256(b'fixture').hexdigest()
            package = root/(digest+'.tar.gz')
            package.write_bytes(b'fixture')
            (root/'current.json').write_text(json.dumps({'schema':2,'sequence':1,'revision':'a'*40,
                'bundle_sha256':digest,'bundle_path':str(package)}))
            (root/'current.json').chmod(0o600)
            with patch('logbook.repair_cycle.request_job_ids',return_value=[1]), \
                 patch('logbook.repair_cycle.discover_missing_jobs',return_value={
                     'container_id':'b'*64,'pid':42,'missing_job_ids':[1]}), \
                 patch('logbook.repair_cycle.prepare_named',side_effect=['failed','prepared']), \
                 patch('logbook.repair_cycle.run_named',return_value='rolled-back'):
                with self.assertRaisesRegex(RuntimeError,'preparation failed'):
                    repair_cycle(root)
                self.assertEqual(repair_cycle(root),'preparation-interrupted')
                with self.assertRaisesRegex(RuntimeError,'not retained'):
                    repair_cycle(root)
            self.assertFalse((root/'repair-cursor.json').exists())

    def test_unfinished_cycle_is_reconciled_before_new_work(self):
        from unittest.mock import Mock
        from logbook.repair_cycle import repair_cycle
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            state=root/('repair-'+'a'*32+'.json')
            state.write_text('{}')
            state.chmod(0o600)
            marker=root/'repair-pending.json'
            marker.write_text(json.dumps({'phase':'running','state':state.name}))
            marker.chmod(0o600)
            from logbook.lock_guard_client import OPERATOR_KEY
            remote=Mock(host='logbook-repair@odin',identity=OPERATOR_KEY)
            remote.recovered.side_effect=[False,True]
            remote.cancel_unstarted.return_value=True
            remote.outcome.return_value='not-retained'
            with patch('logbook.repair_cycle._named_remote',return_value=remote), \
                 patch('logbook.repair_cycle.release_named_handoff') as release, \
                 patch('logbook.repair_cycle.request_job_ids') as source:
                self.assertEqual(repair_cycle(root),'not-retained')
                remote.cancel_unstarted.assert_called_once()
                release.assert_called_once_with(remote)
                source.assert_not_called()
            self.assertFalse(marker.exists())

    def test_interrupted_preparation_clears_marker_without_launch(self):
        from logbook.repair_cycle import repair_cycle
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            marker=root/'repair-pending.json'
            marker.write_text(json.dumps({'phase':'preparing','state':'repair-'+'a'*32+'.json'}))
            marker.chmod(0o600)
            with patch('logbook.repair_cycle.request_job_ids') as source:
                self.assertEqual(repair_cycle(root),'preparation-interrupted')
                source.assert_not_called()
            self.assertFalse(marker.exists())

    def test_simultaneous_cycles_do_not_run(self):
        import fcntl
        from logbook.repair_cycle import repair_cycle
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (root/'repair-cycle.lock').open('w') as lock:
                (root/'repair-cycle.lock').chmod(0o600)
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaises(BlockingIOError):
                    repair_cycle(root)

    def test_discovery_failure_does_not_advance_cursor(self):
        from logbook.repair_cycle import repair_cycle
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('logbook.repair_cycle.request_job_ids',return_value=[1]), \
                 patch('logbook.repair_cycle.discover_missing_jobs',side_effect=ConnectionError('offline')):
                with self.assertRaises(ConnectionError):
                    repair_cycle(root)
            self.assertFalse((root/'repair-cursor.json').exists())
