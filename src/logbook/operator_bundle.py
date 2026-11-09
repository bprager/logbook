"""Build and provision the experimental maintenance bundle over SSH."""
from __future__ import annotations

import hashlib
import gzip
import io
import re
import shlex
import subprocess
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


PINNED_WHEELS = {
    'neo4j': ('neo4j-5.28.4-py3-none-any.whl', 'ba87c423fd2a1fbe6a116c47ac96cfbe7d8c419d24d2f5611e5674283dddf7b9'),
    'pytz': ('pytz-2026.2-py2.py3-none-any.whl', '04156e608bee23d3792fd45c94ae47fae1036688e75032eea2e3bf0323d1f126'),
}


def add_pinned_wheels(archive: tarfile.TarFile, wheel_dir: Path) -> None:
    for package, (filename, expected) in PINNED_WHEELS.items():
        payload = (wheel_dir/filename).read_bytes()
        if hashlib.sha256(payload).hexdigest() != expected:
            raise ValueError('dependency wheel checksum mismatch')
        with zipfile.ZipFile(io.BytesIO(payload)) as wheel:
            for member in wheel.infolist():
                path = PurePosixPath(member.filename)
                if not path.parts or path.parts[0] != package or member.is_dir():
                    continue
                if '..' in path.parts or member.file_size > 60000000:
                    raise ValueError('invalid wheel member')
                data = wheel.read(member)
                info = tarfile.TarInfo(str(path))
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))


def _include(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    if '__pycache__' in Path(info.name).parts or info.name.endswith(('.pyc', '.pyo')):
        return None
    if not (info.isfile() or info.isdir()):
        raise ValueError('bundle contains unsupported file type')
    return info


def _bounded(output: io.BytesIO) -> bytes:
    data = output.getvalue()
    if len(data) > 30000000:
        raise ValueError('bundle exceeds size limit')
    return data


def build_bundle(packages: dict[str, Path]) -> bytes:
    if set(packages) != {'logbook', 'neo4j', 'pytz'}:
        raise ValueError('expected maintenance packages')
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        for name, path in packages.items():
            archive.add(path, arcname=name, filter=_include)
    return _bounded(output)


def verify_checkout(repo: Path, revision: str) -> None:
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('full commit revision required')
    diff = subprocess.run(['git', '-C', str(repo), 'diff', '--quiet', revision, '--', 'src/logbook'])
    untracked = subprocess.run(['git', '-C', str(repo), 'ls-files', '--others', '--exclude-standard',
                                '--', 'src/logbook'], capture_output=True, check=True).stdout
    if diff.returncode != 0 or untracked:
        raise ValueError('checkout does not match selected revision')


def build_revision_bundle(repo: Path, revision: str, wheel_dir: Path) -> bytes:
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('full commit revision and expected dependencies required')
    kind = subprocess.run(['git', '-C', str(repo), 'cat-file', '-t', revision],
                          capture_output=True, check=True).stdout.strip()
    if kind != b'commit':
        raise ValueError('revision must identify a commit')
    source = subprocess.run(['git', '-C', str(repo), 'archive', '--format=tar', revision,
                             'src/logbook'], capture_output=True, check=True).stdout
    output = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(source), mode='r:') as original, \
         gzip.GzipFile(fileobj=output, mode='wb', mtime=0) as compressed, \
         tarfile.open(fileobj=compressed, mode='w') as bundle:
        for item in original:
            if item.name.rstrip('/') == 'src':
                continue
            if not (item.isfile() or item.isdir()):
                raise ValueError('source contains unsupported file type')
            item.name = item.name.removeprefix('src/')
            bundle.addfile(item, original.extractfile(item) if item.isfile() else None)
        add_pinned_wheels(bundle, wheel_dir)
    return _bounded(output)



def build_producer_bundle(repo: Path, revision: str) -> bytes:
    """Package committed producer code and launcher, never mutable checkout contents."""
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('full commit revision required')
    kind = subprocess.run(['git', '-C', str(repo), 'cat-file', '-t', revision],
                          capture_output=True, check=True).stdout.strip()
    if kind != b'commit':
        raise ValueError('revision must identify a commit')
    source = subprocess.run(['git', '-C', str(repo), 'archive', '--format=tar', revision,
                             'src/logbook', 'scripts/logbook-plan-producer'],
                            capture_output=True, check=True).stdout
    output = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(source), mode='r:') as original, \
         tarfile.open(fileobj=output, mode='w') as bundle:
        for item in original:
            if item.name.rstrip('/') in {'src', 'scripts'}:
                continue
            if not (item.isfile() or item.isdir()):
                raise ValueError('source contains unsupported file type')
            if item.name == 'scripts/logbook-plan-producer':
                item.name = 'launcher'
            elif item.name.startswith('src/logbook/') or item.name.rstrip('/') == 'src/logbook':
                item.name = item.name.removeprefix('src/')
            else:
                raise ValueError('unexpected producer package path')
            bundle.addfile(item, original.extractfile(item) if item.isfile() else None)
    return _bounded(output)


def provision_bundle(host: str, identity: Path, destination: Path, staging: Path, data: bytes) -> str:
    from logbook.graph_maintenance_cross_host import SshMaintenance
    SshMaintenance(host, identity, Path('/unused'), {})  # validate host and identity
    if not destination.is_absolute() or staging.parent != destination.parent or staging == destination:
        raise ValueError('absolute sibling bundle and staging paths required')
    digest = hashlib.sha256(data).hexdigest()
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        member = archive.getmember('logbook/operator_bundle_bootstrap.py')
        if not member.isfile() or member.size > 100000:
            raise ValueError('invalid bundle bootstrap')
        source = archive.extractfile(member)
        if source is None:
            raise ValueError('missing bundle bootstrap')
        bootstrap = source.read().decode('utf-8')
    command = shlex.join(['sudo', '-n', '/usr/bin/python3', '-c', bootstrap,
                          str(destination), str(staging), digest])
    result = subprocess.run(['ssh', '-i', str(identity), '-o', 'BatchMode=yes', '-o',
                             'ConnectTimeout=5', '-o', 'StrictHostKeyChecking=yes', host, command],
                            input=data, capture_output=True, timeout=60, check=True)
    if result.stdout.strip() != b'BUNDLE_INSTALLED':
        raise RuntimeError('unexpected bundle installer response')
    return digest


def provision_named_bundle(host: str, identity: Path, destination: Path, staging: Path, data: bytes) -> str:
    """Call a preinstalled trusted named installer; never transmit root code."""
    from logbook.graph_maintenance_cross_host import SshMaintenance
    SshMaintenance(host, identity, Path('/unused'), {})
    parent = Path('/var/lib/logbook-graph-guard/bundles')
    match = re.fullmatch(r'bundle-([a-f0-9]{32})', destination.name)
    if not match or destination.parent != parent or staging.parent != parent or staging.name != 'window-'+match.group(1):
        raise ValueError('named bundle paths rejected')
    command = shlex.join(['sudo', '-n', '/usr/local/libexec/logbook-repair-bundle-installer',
                          str(destination), str(staging)])
    result = subprocess.run(['ssh', '-i', str(identity), '-o', 'BatchMode=yes', '-o',
                             'ConnectTimeout=5', '-o', 'StrictHostKeyChecking=yes', host, command],
                            input=data, capture_output=True, timeout=60, check=True)
    if result.stdout.strip() != b'BUNDLE_INSTALLED':
        raise RuntimeError('unexpected bundle installer response')
    return hashlib.sha256(data).hexdigest()
