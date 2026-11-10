"""Deliver signed release metadata through the existing restricted SSH route."""
from __future__ import annotations

import json
import hashlib
import fcntl
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from logbook.graph_maintenance_worker import private_directory
from logbook.lock_guard_client import OPERATOR_KEY
from logbook.operator_entry import read_release_bundle

REPOSITORY = 'https://github.com/bprager/logbook.git'


def fetch_candidate(repository: str) -> tuple[bytes, bytes, bytes]:
    """Read a bounded candidate whose parent is the currently observed main."""
    with tempfile.TemporaryDirectory(prefix='logbook-release-fetch-') as temporary:
        repo = Path(temporary)/'repo.git'
        subprocess.run(['git','init','--bare','--quiet',str(repo)],check=True,capture_output=True,timeout=30)
        def git(*args: str, timeout: int = 30) -> bytes:
            return subprocess.run(['git','-C',str(repo),'-c','http.sslVerify=true','-c','credential.helper=',
                                   '-c','protocol.file.allow=always',*args],check=True,capture_output=True,timeout=timeout).stdout
        git('fetch','--quiet','--no-tags','--depth=2',repository,
            '+refs/heads/main:refs/remotes/origin/main',
            '+refs/heads/repair-release-candidates:refs/remotes/origin/candidate',timeout=120)
        candidate = 'refs/remotes/origin/candidate'
        def blob(name: str, limit: int) -> bytes:
            ref = candidate+':.logbook-repair/'+name
            size = int(git('cat-file','-s',ref).strip())
            if not 1 <= size <= limit:
                raise ValueError('candidate file exceeds limit')
            return git('show',ref)
        manifest = blob('release.json',100000)
        signature = blob('release.json.sig',10000)
        data = blob('bundle.tar.gz',30000000)
        try:
            item = json.loads(manifest)
            revision = item['revision']
            if not isinstance(revision, str) or not re.fullmatch(r'[a-f0-9]{40}',revision):
                raise ValueError('invalid candidate revision')
            if (git('rev-parse',candidate+'^').strip().decode() != revision
                    or git('rev-parse','refs/remotes/origin/main').strip().decode() != revision):
                raise ValueError('candidate does not match current main')
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError) as error:
            raise ValueError('invalid candidate manifest') from error
        return manifest,signature,data


def send_release(manifest: bytes, signature: bytes) -> str:
    if (not isinstance(manifest, bytes) or not 1 <= len(manifest) <= 100000
            or not isinstance(signature, bytes) or not 1 <= len(signature) <= 10000):
        raise ValueError('bounded release metadata required')
    try:
        request = json.dumps({'manifest':manifest.decode('utf-8'), 'signature':signature.decode('utf-8')})
    except UnicodeDecodeError as error:
        raise ValueError('invalid release metadata') from error
    try:
        result = subprocess.run(['ssh', '-i', str(OPERATOR_KEY), '-o', 'BatchMode=yes',
                                 '-o', 'ConnectTimeout=5', '-o', 'StrictHostKeyChecking=yes',
                                 'logbook-repair@odin', 'sudo -n /usr/local/libexec/logbook-repair-release-receiver'],
                                input=request, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired as error:
        raise TimeoutError('release receiver timed out') from error
    response = result.stdout.strip()
    if result.returncode or response not in {'UPDATED', 'UNCHANGED'}:
        raise ConnectionError('release receiver rejected metadata')
    return response


def accept_candidate(root: Path, manifest: bytes, signature: bytes, data: bytes) -> dict:
    """Store exact bytes, ask the protected receiver, then switch local state."""
    private_directory(root)
    fd = os.open(root/'delivery.lock',os.O_RDWR | os.O_CREAT | getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'r+b') as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('private delivery lock required')
        fcntl.flock(lock,fcntl.LOCK_EX)
        return _accept_candidate_locked(root,manifest,signature,data)


def _accept_candidate_locked(root: Path, manifest: bytes, signature: bytes, data: bytes) -> dict:
    if (not isinstance(manifest, bytes) or not 1 <= len(manifest) <= 100000
            or not isinstance(signature, bytes) or not 1 <= len(signature) <= 10000
            or not isinstance(data, bytes) or not 1 <= len(data) <= 30000000):
        raise ValueError('bounded release candidate required')
    try:
        item = json.loads(manifest)
        if (not isinstance(item, dict) or set(item) != {'schema','revision','bundle_sha256','sequence'}
                or type(item['schema']) is not int or item['schema'] != 2
                or type(item['sequence']) is not int or not 1 <= item['sequence'] <= 9223372036854775807
                or not isinstance(item['revision'], str) or not re.fullmatch(r'[a-f0-9]{40}',item['revision'])
                or not isinstance(item['bundle_sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}',item['bundle_sha256'])
                or hashlib.sha256(data).hexdigest() != item['bundle_sha256']):
            raise ValueError('invalid release candidate')
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError('invalid release candidate') from error
    package = root/(item['bundle_sha256']+'.tar.gz')
    try:
        fd = os.open(package, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    except FileExistsError:
        if hashlib.sha256(read_release_bundle(package)).hexdigest() != item['bundle_sha256']:
            raise ValueError('existing release package mismatch')
    else:
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        fd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    send_release(manifest,signature)
    result = {**item, 'bundle_path':str(package)}
    fd, temporary = tempfile.mkstemp(prefix='.release-state-', dir=root)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(result,output,sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary,root/'current.json')
        fd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return result


def main() -> None:  # pragma: no cover
    if len(sys.argv) != 1:
        raise SystemExit('no arguments permitted')
    manifest, signature, data = fetch_candidate(REPOSITORY)
    result = accept_candidate(OPERATOR_KEY.parent,manifest,signature,data)
    print('RELEASE_READY',result['revision'])


if __name__ == '__main__':  # pragma: no cover
    main()
