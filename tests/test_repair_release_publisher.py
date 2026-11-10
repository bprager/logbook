import hashlib
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase


class RepairReleasePublisherTests(TestCase):
    def test_release_workflow_only_signs_merged_current_main(self):
        workflow=(Path(__file__).resolve().parents[1]/'.github/workflows/repair-release.yml').read_text()
        self.assertIn('  pull_request_target:\n    types: [closed]\n    branches: [main]',workflow)
        self.assertIn('if: github.event.pull_request.merged == true',workflow)
        self.assertIn('ref: ${{ github.event.pull_request.merge_commit_sha }}',workflow)
        verification=workflow.index('name: Verify the merged revision is current main')
        signing=workflow.index('name: Build and sign the exact release')
        self.assertLess(verification,signing)
        self.assertIn('git ls-remote origin refs/heads/main',workflow[verification:signing])

    def test_publisher_creates_release_verified_by_existing_installer(self):
        from logbook.operator_bundle_bootstrap import approved_release
        from logbook.repair_release_publisher import publish_release
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            key = root/'release-key'
            subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
            payload = b'synthetic package'
            release = root/'release'
            publish_release(payload, 'a'*40, key, release, sequence=12)
            self.assertEqual((release/'bundle.tar.gz').read_bytes(), payload)
            allowed = release/'release.allowed_signers'
            allowed.write_text('release '+key.with_suffix('.pub').read_text())
            allowed.chmod(0o600)
            self.assertEqual(approved_release(release), {'schema':2, 'revision':'a'*40, 'sequence':12,
                                                         'bundle_sha256':hashlib.sha256(payload).hexdigest()})
            (release/'release.json').write_text('{}')
            with self.assertRaises(ValueError):
                approved_release(release)
            with self.assertRaises(FileExistsError):
                publish_release(payload, 'a'*40, key, release, sequence=12)
            with self.assertRaises(ValueError):
                publish_release(payload, 'bad', key, root/'bad', sequence=12)
            self.assertFalse((root/'bad').exists())
            with self.assertRaises(subprocess.CalledProcessError):
                publish_release(payload, 'a'*40, root/'missing-key', root/'failed', sequence=12)
            self.assertFalse((root/'failed').exists())
            with self.assertRaises(ValueError):
                publish_release(payload, 'a'*40, key, root/'bad-sequence', sequence=0)
            shutil.rmtree(release)
