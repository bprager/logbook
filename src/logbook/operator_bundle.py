"""Build and provision the experimental maintenance bundle over SSH."""
from __future__ import annotations

import hashlib
import io
import re
import shlex
import subprocess
import tarfile
from pathlib import Path


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


def build_revision_bundle(repo: Path, revision: str, dependencies: dict[str, Path]) -> bytes:
    if not re.fullmatch(r'[0-9a-f]{40}', revision) or set(dependencies) != {'neo4j', 'pytz'}:
        raise ValueError('full commit revision and expected dependencies required')
    kind = subprocess.run(['git', '-C', str(repo), 'cat-file', '-t', revision],
                          capture_output=True, check=True).stdout.strip()
    if kind != b'commit':
        raise ValueError('revision must identify a commit')
    source = subprocess.run(['git', '-C', str(repo), 'archive', '--format=tar', revision,
                             'src/logbook'], capture_output=True, check=True).stdout
    output = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(source), mode='r:') as original, \
         tarfile.open(fileobj=output, mode='w:gz') as bundle:
        for item in original:
            if item.name.rstrip('/') == 'src':
                continue
            if not (item.isfile() or item.isdir()):
                raise ValueError('source contains unsupported file type')
            item.name = item.name.removeprefix('src/')
            bundle.addfile(item, original.extractfile(item) if item.isfile() else None)
        for name, path in dependencies.items():
            bundle.add(path, arcname=name, filter=_include)
    return _bounded(output)


def provision_bundle(host: str, identity: Path, destination: Path, staging: Path, data: bytes) -> str:
    from logbook.graph_maintenance_cross_host import SshMaintenance
    SshMaintenance(host, identity, Path('/unused'), {})  # validate host and identity
    if not destination.is_absolute() or staging.parent != destination.parent or staging == destination:
        raise ValueError('absolute sibling bundle and staging paths required')
    digest = hashlib.sha256(data).hexdigest()
    bootstrap = Path(__file__).with_name('operator_bundle_bootstrap.py').read_text()
    command = shlex.join(['sudo', '-n', '/usr/bin/python3', '-c', bootstrap,
                          str(destination), str(staging), digest])
    result = subprocess.run(['ssh', '-i', str(identity), '-o', 'BatchMode=yes', '-o',
                             'ConnectTimeout=5', '-o', 'StrictHostKeyChecking=yes', host, command],
                            input=data, capture_output=True, timeout=60, check=True)
    if result.stdout.strip() != b'BUNDLE_INSTALLED':
        raise RuntimeError('unexpected bundle installer response')
    return digest
