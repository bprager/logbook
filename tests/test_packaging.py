from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from zipfile import ZipFile

from hatchling.builders.wheel import WheelBuilder


class PackagingTests(TestCase):
    def test_wheel_contains_remote_agent_and_observer_assets_once(self):
        repo = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary:
            builder = WheelBuilder(str(repo))
            wheel = next(builder.build(directory=temporary, versions=['standard']))
            with ZipFile(wheel) as archive:
                names = archive.namelist()
                self.assertEqual(len(names), len(set(names)))
                self.assertIn('logbook/remote_cli.py', names)
                static = repo / 'src' / 'logbook' / 'static' / 'watch'
                files = [path for path in static.rglob('*') if path.is_file()]
                self.assertTrue(files)
                for path in files:
                    name = path.relative_to(repo / 'src').as_posix()
                    self.assertEqual(names.count(name), 1, name)
                    self.assertEqual(archive.read(name), path.read_bytes())
                entry_points = next(name for name in names if name.endswith('.dist-info/entry_points.txt'))
                self.assertIn(b'logbook-remote = logbook.remote_cli:main', archive.read(entry_points))
