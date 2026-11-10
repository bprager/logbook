"""Create a signed Logbook repair release without publishing or activating it."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from logbook.operator_bundle import build_revision_bundle


def publish_release(data: bytes, revision: str, key: Path, destination: Path) -> None:
    if (not isinstance(revision, str) or not re.fullmatch(r'[a-f0-9]{40}', revision)
            or not isinstance(data, bytes) or not data or len(data) > 30000000):
        raise ValueError('bounded package and full revision required')
    destination.mkdir(mode=0o700)
    try:
        payload = destination/'bundle.tar.gz'
        manifest = destination/'release.json'
        payload.write_bytes(data)
        payload.chmod(0o600)
        manifest.write_text(json.dumps({'schema':1, 'revision':revision,
                                        'bundle_sha256':hashlib.sha256(data).hexdigest()},
                                       sort_keys=True, separators=(',', ':'))+'\n')
        manifest.chmod(0o600)
        subprocess.run(['/usr/bin/ssh-keygen', '-Y', 'sign', '-n', 'logbook-repair-release',
                        '-f', str(key), str(manifest)], check=True, capture_output=True, timeout=30)
        (destination/'release.json.sig').chmod(0o600)
    except Exception:
        shutil.rmtree(destination)
        raise


def main() -> None:  # pragma: no cover
    if len(sys.argv) != 5:
        raise SystemExit('expected revision, wheel directory, signing key, output directory')
    revision, wheels, key, output = sys.argv[1:]
    repo = Path(__file__).resolve().parents[2]
    data = build_revision_bundle(repo, revision, Path(wheels))
    publish_release(data, revision, Path(key), Path(output))
    print('RELEASE_CREATED')


if __name__ == '__main__':  # pragma: no cover
    main()
