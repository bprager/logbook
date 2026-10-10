"""Separate remote settings: no Odin, vault or OpenClaw credentials on a Mac."""
from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in
                         ('127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12',
                          '192.168.0.0/16', '100.64.0.0/10', '::1/128', 'fc00::/7'))


def private_address(value):
    address = ipaddress.ip_address(value)
    return any(address in network for network in PRIVATE_NETWORKS)


def read_secret(path: Path):
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError('credential file must be private (mode 0600) and not a symlink')
    return path.read_text().strip()


@dataclass(frozen=True)
class ServerConfig:
    ledger_path: Path
    root: Path
    credentials_file: Path
    bind_host: str = '127.0.0.1'
    port: int = 8790
    vpn_transport: bool = False
    max_bytes: int = 2 * 1024**3
    quota_bytes: int = 20 * 1024**3
    reserve_bytes: int = 1024**3
    chunk_bytes: int = 1024**2
    requests_per_minute: int = 600
    max_active_uploads: int = 128
    body_timeout_seconds: int = 30

    def validate(self):
        if type(self.vpn_transport) is not bool:
            raise ValueError('vpn_transport must be a JSON boolean')
        if not private_address(self.bind_host):
            raise ValueError('ingest must bind to an explicit private IP address')
        if not ipaddress.ip_address(self.bind_host).is_loopback and not self.vpn_transport:
            raise ValueError('non-loopback ingestion requires explicitly configured VPN transport')
        for value in (self.max_bytes, self.quota_bytes, self.chunk_bytes, self.requests_per_minute,
                      self.max_active_uploads, self.body_timeout_seconds):
            if type(value) is not int or value <= 0:
                raise ValueError('ingest limits must be positive integers')
        if self.reserve_bytes < 0 or not 1 <= self.port <= 65535:
            raise ValueError('invalid reserve or port')
        self.credentials()

    def credentials(self):
        values = json.loads(read_secret(self.credentials_file))
        if not isinstance(values, dict) or any(
            not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', key) or
            not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value)
            for key, value in values.items()
        ) or len(set(values.values())) != len(values):
            raise ValueError('invalid device credential registry')
        return values


@dataclass(frozen=True)
class AgentConfig:
    root: Path
    mount_path: Path
    volume_name: str
    volume_uuid: str
    recordings_path: str
    timezone: str
    server_url: str
    token_file: Path
    vpn_transport: bool = False
    stable_seconds: int = 5
    reserve_bytes: int = 1024**3
    max_bytes: int = 2 * 1024**3
    chunk_bytes: int = 1024**2

    def validate(self):
        if type(self.vpn_transport) is not bool:
            raise ValueError('vpn_transport must be a JSON boolean')
        url = urlsplit(self.server_url)
        if (url.scheme not in ('http', 'https') or not url.hostname or url.username
                or url.password or url.query or url.fragment or url.path not in ('', '/')):
            raise ValueError('invalid ingest URL')
        if url.scheme == 'http' and not (
            private_address(url.hostname) and
            (ipaddress.ip_address(url.hostname).is_loopback or self.vpn_transport)
        ):
            raise ValueError('HTTP requires an explicit VPN IP (or loopback for testing)')
        if not self.volume_uuid or self.mount_path.name != self.volume_name:
            raise ValueError('configure the recorder volume name and UUID')
        path = Path(self.recordings_path)
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('recordings_path must be relative to the Sony volume')
        if (self.stable_seconds < 0 or self.reserve_bytes < 0 or
                self.max_bytes <= 0 or self.chunk_bytes <= 0):
            raise ValueError('invalid agent limits')
        ZoneInfo(self.timezone)
        token = read_secret(self.token_file)
        if len(token) < 32 or any(c.isspace() for c in token):
            raise ValueError('device token must contain at least 32 non-whitespace characters')


def load_remote_config(path: Path, kind):
    values = json.loads(path.read_text())
    for key in ('ledger_path', 'root', 'credentials_file', 'mount_path', 'token_file'):
        if key in values:
            value = Path(values[key]).expanduser()
            values[key] = value if value.is_absolute() else path.resolve().parent / value
    config = kind(**values)
    config.validate()
    return config
