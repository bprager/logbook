"""Standard-library-only installer for a private, validated maintenance bundle."""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import stat
import sys
import tarfile
from pathlib import Path, PurePosixPath


def install_bundle(data: bytes, destination: Path, digest: str) -> None:
    if len(data) > 30000000 or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('bundle digest or size invalid')
    parent = destination.parent.lstat()
    if not destination.is_absolute() or not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
        raise ValueError('private bundle parent required')
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as archive:
        members = archive.getmembers()
        if sum(member.size for member in members) > 60000000:
            raise ValueError('bundle too large')
        for member in members:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or '..' in path.parts or not path.parts
                    or path.parts[0] not in {'logbook', 'neo4j', 'pytz'}
                    or not (member.isfile() or member.isdir())):
                raise ValueError('unsafe bundle path or type')
        destination.mkdir(mode=0o700)
        try:
            for member in members:
                path = destination.joinpath(*PurePosixPath(member.name).parts)
                if member.isdir():
                    path.mkdir(mode=0o700, parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    source = archive.extractfile(member)
                    if source is None:
                        raise ValueError('missing bundle contents')
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, 'wb') as output:
                        shutil.copyfileobj(source, output)
            (destination/'.bundle-ready').write_text(digest+'\n')
        except Exception:
            shutil.rmtree(destination)
            raise


if __name__ == '__main__':  # pragma: no cover
    if os.geteuid() != 0 or len(sys.argv) != 4:
        raise SystemExit('root, destination, staging and digest required')
    destination, staging = Path(sys.argv[1]), Path(sys.argv[2])
    if staging.parent != destination.parent or staging == destination:
        raise SystemExit('bundle and staging must be distinct siblings')
    install_bundle(sys.stdin.buffer.read(30000001), destination, sys.argv[3])
    staging.mkdir(mode=0o700)
    print('BUNDLE_INSTALLED')
