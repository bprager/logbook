from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    """Raised when required local configuration is missing or invalid."""


@dataclass(frozen=True)
class RecorderConfig:
    volume_name: str
    mount_path: Path
    recordings_path: str
    device_path: str | None = None

    @property
    def recordings_dir(self) -> Path:
        return self.mount_path / self.recordings_path.lstrip("/")


@dataclass(frozen=True)
class AppConfig:
    processing_root: Path
    sqlite_path: Path
    recorder: RecorderConfig
    odin: "OdinConfig"
    obsidian: "ObsidianConfig | None" = None
    api: "ApiConfig | None" = None


@dataclass(frozen=True)
class OdinConfig:
    api_base_url: str
    api_token: str | None
    asr_model: str
    asr_device: str
    asr_compute_type: str
    asr_vad_filter: bool
    diarization_model: str


@dataclass(frozen=True)
class ObsidianConfig:
    cli_bin: str
    vault_repo_url: str
    vault_local_path: Path
    vault_name: str | None = None
    sync_command: str | None = None
    stage_command: str | None = None
    status_command: str | None = None
    commit_command: str | None = None
    push_command: str | None = None


@dataclass(frozen=True)
class ApiConfig:
    bind_host: str
    port: int
    read_token: str | None = None
    action_token: str | None = None


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        raise ConfigError(f"env file not found: {path}")

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f"invalid .env line {line_number}: expected KEY=VALUE")

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ConfigError(f"invalid .env line {line_number}: empty key")
        values[key] = _strip_quotes(value)

    return values


def load_recorder_config(env_path: Path) -> RecorderConfig:
    values = parse_env_file(env_path)
    return recorder_config_from_values(values)


def load_app_config(env_path: Path) -> AppConfig:
    values = parse_env_file(env_path)
    processing_root = Path(_required(values, "LOGBOOK_PROCESSING_ROOT"))
    sqlite_path = Path(values.get("LOGBOOK_SQLITE_PATH") or processing_root / "voice_ingest.sqlite")

    return AppConfig(
        processing_root=processing_root,
        sqlite_path=sqlite_path,
        recorder=recorder_config_from_values(values),
        odin=odin_config_from_values(values),
        obsidian=obsidian_config_from_values(values),
        api=api_config_from_values(values),
    )


def recorder_config_from_values(values: dict[str, str]) -> RecorderConfig:
    volume_name = _required(values, "SONY_RECORDER_VOLUME_NAME")
    mount_path = _required(values, "SONY_RECORDER_MOUNT_PATH")
    recordings_path = _required(values, "SONY_RECORDER_RECORDINGS_PATH")
    device_path = values.get("SONY_RECORDER_DEVICE_PATH") or None

    return RecorderConfig(
        volume_name=volume_name,
        mount_path=Path(mount_path),
        recordings_path=recordings_path,
        device_path=device_path,
    )


def odin_config_from_values(values: dict[str, str]) -> OdinConfig:
    return OdinConfig(
        api_base_url=_required(values, "ODIN_API_BASE_URL").rstrip("/"),
        api_token=values.get("ODIN_API_TOKEN") or None,
        asr_model=values.get("ODIN_ASR_MODEL") or "large-v3",
        asr_device=values.get("ODIN_ASR_DEVICE") or "cuda",
        asr_compute_type=values.get("ODIN_ASR_COMPUTE_TYPE") or "float16",
        asr_vad_filter=_parse_bool(values.get("ODIN_ASR_VAD_FILTER", "true")),
        diarization_model=values.get("ODIN_DIARIZATION_MODEL")
        or "pyannote/speaker-diarization-3.1",
    )


def obsidian_config_from_values(values: dict[str, str]) -> ObsidianConfig:
    processing_root = Path(values.get("LOGBOOK_PROCESSING_ROOT") or ".")
    return ObsidianConfig(
        cli_bin=values.get("OBSIDIAN_CLI_BIN") or "obsidian",
        vault_repo_url=values.get("OBSIDIAN_VAULT_REPO_URL")
        or "https://github.com/bprager/obs-vault.git",
        vault_local_path=Path(
            values.get("OBSIDIAN_VAULT_LOCAL_PATH")
            or processing_root / "test-vault"
        ),
        vault_name=_optional(values, "OBSIDIAN_VAULT_NAME"),
        sync_command=_optional(values, "OBSIDIAN_SYNC_COMMAND"),
        stage_command=_optional(values, "OBSIDIAN_STAGE_COMMAND"),
        status_command=_optional(values, "OBSIDIAN_STATUS_COMMAND"),
        commit_command=_optional(values, "OBSIDIAN_COMMIT_COMMAND"),
        push_command=_optional(values, "OBSIDIAN_PUSH_COMMAND"),
    )


def api_config_from_values(values: dict[str, str]) -> ApiConfig:
    return ApiConfig(
        bind_host=values.get("LOGBOOK_API_BIND_HOST") or "127.0.0.1",
        port=int(values.get("LOGBOOK_API_PORT") or "8765"),
        read_token=_optional(values, "LOGBOOK_READ_TOKEN"),
        action_token=_optional(values, "LOGBOOK_ACTION_TOKEN"),
    )


def _required(values: dict[str, str], key: str) -> str:
    value = values.get(key, "").strip()
    if not value:
        raise ConfigError(f"missing required config: {key}")
    return value


def _optional(values: dict[str, str], key: str) -> str | None:
    value = values.get(key, "").strip()
    return value or None


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}
