"""Build and provision the experimental maintenance bundle over SSH."""
from __future__ import annotations

import hashlib
import io
import shlex
import subprocess
import tarfile
from pathlib import Path


def build_bundle(packages: dict[str, Path]) -> bytes:
    if set(packages) != {'logbook', 'neo4j', 'pytz'}:
        raise ValueError('expected maintenance packages')
    output = io.BytesIO()
    def include(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        if '__pycache__' in Path(info.name).parts or info.name.endswith(('.pyc', '.pyo')):
            return None
        if not (info.isfile() or info.isdir()):
            raise ValueError('bundle contains unsupported file type')
        return info
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        for name, path in packages.items():
            archive.add(path, arcname=name, filter=include)
    data = output.getvalue()
    if len(data) > 30000000:
        raise ValueError('bundle exceeds size limit')
    return data


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
