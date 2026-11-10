import sqlite3
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from logbook.graph_maintenance_worker import load_plan
from logbook.memory_graph import MemoryGraphPlan, MemoryNode
from logbook.operator_plan import prepare_job_plan


class OperatorPlanTests(unittest.TestCase):
    def test_closed_wal_fallback_uses_sqlite_backup_and_preserves_data(self):
        from logbook.operator_plan import _backup_ledger

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'source.sqlite'
            db = sqlite3.connect(source)
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE sample(value TEXT)')
            db.execute("INSERT INTO sample VALUES ('saved')")
            db.commit()
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            db.close()
            self.assertFalse(Path(str(source)+'-wal').exists())
            real_connect = sqlite3.connect
            modes = []

            def connect(database, *args, **kwargs):
                if isinstance(database, str) and '?mode=' in database:
                    mode = database.rsplit('=', 1)[-1]
                    modes.append(mode)
                    if mode == 'ro':
                        raise sqlite3.OperationalError('unable to open database file')
                return real_connect(database, *args, **kwargs)

            with patch('logbook.operator_plan.sqlite3.connect', side_effect=connect):
                _backup_ledger(source, root/'snapshot.sqlite')
            self.assertEqual(modes, ['ro', 'rw'])
            with real_connect(root/'snapshot.sqlite') as copied:
                self.assertEqual(copied.execute('SELECT value FROM sample').fetchone()[0], 'saved')
            with real_connect(source) as original:
                self.assertEqual(original.execute('SELECT value FROM sample').fetchone()[0], 'saved')

    def test_wal_fallback_does_not_retry_other_errors_or_existing_sidecars(self):
        from logbook.operator_plan import _backup_ledger

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'source.sqlite'
            db = sqlite3.connect(source)
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE sample(value TEXT)')
            db.commit()
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            db.close()
            real_connect = sqlite3.connect
            for error, sidecar in [('database is malformed', False), ('unable to open database file', True)]:
                modes = []
                wal = Path(str(source)+'-wal')
                if sidecar:
                    wal.touch()

                def connect(database, *args, **kwargs):
                    if isinstance(database, str) and '?mode=' in database:
                        modes.append(database.rsplit('=', 1)[-1])
                        raise sqlite3.OperationalError(error)
                    return real_connect(database, *args, **kwargs)

                with self.subTest(error=error), patch('logbook.operator_plan.sqlite3.connect', side_effect=connect):
                    with self.assertRaises(sqlite3.OperationalError):
                        _backup_ledger(source, root/'snapshot.sqlite')
                self.assertEqual(modes, ['ro'])
                if sidecar:
                    wal.unlink()

    def test_repair_artifact_copy_rejects_unsafe_changed_and_oversized_inputs(self):
        from logbook.operator_plan import _copy_artifact

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'source.json'
            source.write_bytes(b'{}')
            destination = root/'copy.json'
            with self.assertRaisesRegex(ValueError, 'outside'):
                _copy_artifact(root, Path('../source.json'), destination)
            info = source.stat()
            values = {name: getattr(info, name) for name in
                      ('st_mode', 'st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')}
            before = SimpleNamespace(**values)
            after = SimpleNamespace(**{**values, 'st_mtime_ns': values['st_mtime_ns'] + 1})
            with patch('logbook.operator_plan.os.fstat', side_effect=[before, after]):
                with self.assertRaisesRegex(ValueError, 'changed'):
                    _copy_artifact(root, Path('source.json'), destination)
            with source.open('wb') as output:
                output.truncate(50000001)
            with self.assertRaisesRegex(ValueError, 'invalid'):
                _copy_artifact(root, Path('source.json'), destination)
            self.assertFalse(destination.exists())

    def test_repair_uses_private_copies_of_ledger_artifacts(self):
        from tests.test_memory_graph import _seed_memory_fixture
        from logbook.memory_graph import build_memory_graph_plan

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _seed_memory_fixture(root)
            original = build_memory_graph_plan(config, job_id=1)

            def inspect_snapshot(snapshot_config, job_id):
                self.assertNotEqual(snapshot_config.processing_root, config.processing_root)
                insight = snapshot_config.processing_root/'insights'/'job-000001.insights.json'
                self.assertTrue(insight.is_file())
                with sqlite3.connect(snapshot_config.sqlite_path) as db:
                    row = db.execute('SELECT diarization_path, transcript_path FROM recording_jobs WHERE id=1').fetchone()
                selected = Path(row[0] or row[1])
                self.assertTrue(selected.is_relative_to(snapshot_config.processing_root))
                self.assertTrue(selected.is_file())
                return build_memory_graph_plan(snapshot_config, job_id=job_id)

            with patch('logbook.operator_plan.build_memory_graph_plan', side_effect=inspect_snapshot):
                prepare_job_plan(config, 1, root/'plan.json')
            actual = load_plan(root/'plan.json')
            self.assertEqual(actual.nodes, original.nodes)
            self.assertEqual(actual.relationships, original.relationships)

    def test_rejects_transcript_outside_root_or_through_symlink(self):
        from tests.test_memory_graph import _seed_memory_fixture

        for variant in ('outside', 'symlink'):
            with self.subTest(variant=variant), TemporaryDirectory() as tmp:
                root = Path(tmp)
                config = _seed_memory_fixture(root)
                external = root/'external.json'
                external.write_text('{"text":"external"}')
                selected = external
                if variant == 'symlink':
                    selected = config.processing_root/'linked.json'
                    selected.symlink_to(external)
                with sqlite3.connect(config.sqlite_path) as db:
                    db.execute('UPDATE recording_jobs SET diarization_path=NULL, transcript_path=? WHERE id=1',
                               (str(selected),))
                with self.assertRaises((ValueError, OSError)):
                    prepare_job_plan(config, 1, root/'plan.json')
                self.assertFalse((root/'plan.json').exists())

    def test_missing_referenced_transcript_does_not_produce_partial_plan(self):
        from tests.test_memory_graph import _seed_memory_fixture
        from logbook.ledger import open_ledger

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _seed_memory_fixture(root)
            ledger = open_ledger(config.sqlite_path)
            try:
                job = ledger.get_by_id(1)
            finally:
                ledger.close()
            transcript = Path(job.diarization_path or job.transcript_path)
            transcript.unlink()
            with self.assertRaisesRegex(ValueError, 'transcript'):
                prepare_job_plan(config, 1, root/'plan.json')
            self.assertFalse((root/'plan.json').exists())

    def test_copies_ledger_and_writes_private_non_overwriting_plan(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root/'ledger.db'
            with sqlite3.connect(ledger) as db:
                db.execute('CREATE TABLE sample(value TEXT)')
                db.execute("INSERT INTO sample VALUES ('snapshot')")
                db.execute('CREATE TABLE recording_jobs(id INTEGER, diarization_path TEXT, transcript_path TEXT)')
                db.execute('INSERT INTO recording_jobs VALUES (123, NULL, NULL)')
            node = MemoryNode('logbook:job:000123', ('LogbookJob',), {})
            plan = MemoryGraphPlan((node,), (), 'now')
            def build(snapshot_config, job_id):
                self.assertNotEqual(snapshot_config.sqlite_path, ledger)
                with sqlite3.connect(snapshot_config.sqlite_path) as db:
                    self.assertEqual(db.execute('SELECT value FROM sample').fetchone()[0], 'snapshot')
                self.assertEqual(job_id, 123)
                return plan
            from dataclasses import dataclass
            @dataclass(frozen=True)
            class Config:
                sqlite_path: Path
                processing_root: Path
            with patch('logbook.operator_plan.build_memory_graph_plan', side_effect=build):
                result = prepare_job_plan(Config(ledger, root), 123, root/'plan.json')
                self.assertEqual(result, (1, 0))
                self.assertEqual(load_plan(root/'plan.json'), plan)
                self.assertEqual(stat.S_IMODE((root/'plan.json').stat().st_mode), 0o600)
                with self.assertRaises(FileExistsError):
                    prepare_job_plan(Config(ledger, root), 123, root/'plan.json')

    def test_invalid_job_rejected(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'positive'):
                prepare_job_plan(SimpleNamespace(), 0, Path(tmp)/'plan')

    def test_missing_job_and_oversized_plan_are_not_written(self):
        from dataclasses import dataclass
        @dataclass(frozen=True)
        class Config:
            sqlite_path: Path
            processing_root: Path
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root/'ledger.db'
            with sqlite3.connect(ledger) as db:
                db.execute('CREATE TABLE sample(value TEXT)')
                db.execute('CREATE TABLE recording_jobs(id INTEGER, diarization_path TEXT, transcript_path TEXT)')
            empty = MemoryGraphPlan((), (), 'now')
            large = MemoryGraphPlan((MemoryNode('logbook:job:000123', ('LogbookJob',),
                                                {'payload': 'x'*5000001}),), (), 'now')
            with patch('logbook.operator_plan.build_memory_graph_plan', side_effect=[empty, large]):
                with self.assertRaisesRegex(ValueError, 'not found'):
                    prepare_job_plan(Config(ledger, root), 123, root/'plan')
                with self.assertRaisesRegex(ValueError, 'limit'):
                    prepare_job_plan(Config(ledger, root), 123, root/'plan')
            self.assertFalse((root/'plan').exists())
