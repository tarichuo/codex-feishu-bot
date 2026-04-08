"""Environment-backed configuration models for the Feishu Codex Bot."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping


class ConfigError(ValueError):
    """Raised when application configuration is missing or invalid."""


ENV_PREFIX = "FEISHU_CODEX_BOT_"


def _env_name(name: str) -> str:
    return f"{ENV_PREFIX}{name}"


def _read_required(env: Mapping[str, str], name: str) -> str:
    value = env.get(_env_name(name), "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable: {_env_name(name)}")
    return value


def _read_optional(env: Mapping[str, str], name: str, default: str) -> str:
    value = env.get(_env_name(name))
    if value is None:
        return default
    stripped = value.strip()
    return stripped if stripped else default


def _resolve_path(base_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _parse_csv_set(raw_value: str, *, field_name: str) -> frozenset[str]:
    values = {item.strip() for item in raw_value.split(",") if item.strip()}
    if not values:
        raise ConfigError(f"{field_name} must contain at least one non-empty user id")
    return frozenset(values)


def _validate_log_level(level: str) -> str:
    normalized = level.upper()
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if normalized not in valid_levels:
        raise ConfigError(
            f"Invalid log level '{level}'. Expected one of: {', '.join(sorted(valid_levels))}"
        )
    return normalized


@dataclass(frozen=True, slots=True)
class FeishuConfig:
    app_id: str
    app_secret: str


@dataclass(frozen=True, slots=True)
class CodexConfig:
    server_url: str


@dataclass(frozen=True, slots=True)
class StorageConfig:
    base_dir: Path
    data_dir: Path
    sqlite_path: Path
    media_dir: Path
    logs_dir: Path


@dataclass(frozen=True, slots=True)
class SecurityConfig:
    owner_user_id: str
    allowed_user_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    level: str


@dataclass(frozen=True, slots=True)
class AppConfig:
    feishu: FeishuConfig
    codex: CodexConfig
    storage: StorageConfig
    security: SecurityConfig
    logging: LoggingConfig


def load_config(
    env: Mapping[str, str] | None = None,
    *,
    base_dir: Path | None = None,
) -> AppConfig:
    """Load and validate application configuration from environment variables."""
    env_map = env if env is not None else os.environ
    project_root = (base_dir or Path.cwd()).resolve()

    data_dir = _resolve_path(
        project_root,
        _read_optional(env_map, "DATA_DIR", "var"),
    )
    sqlite_path = _resolve_path(
        project_root,
        _read_optional(env_map, "SQLITE_PATH", str(data_dir / "app.db")),
    )
    media_dir = _resolve_path(
        project_root,
        _read_optional(env_map, "MEDIA_DIR", str(data_dir / "media")),
    )
    logs_dir = _resolve_path(
        project_root,
        _read_optional(env_map, "LOGS_DIR", str(data_dir / "logs")),
    )

    feishu = FeishuConfig(
        app_id=_read_required(env_map, "FEISHU_APP_ID"),
        app_secret=_read_required(env_map, "FEISHU_APP_SECRET"),
    )
    codex = CodexConfig(
        server_url=_read_required(env_map, "CODEX_SERVER_URL"),
    )
    security = SecurityConfig(
        owner_user_id=_read_required(env_map, "OWNER_USER_ID"),
        allowed_user_ids=_parse_csv_set(
            _read_required(env_map, "ALLOWED_USER_IDS"),
            field_name=_env_name("ALLOWED_USER_IDS"),
        ),
    )
    logging = LoggingConfig(
        level=_validate_log_level(_read_optional(env_map, "LOG_LEVEL", "INFO")),
    )
    storage = StorageConfig(
        base_dir=project_root,
        data_dir=data_dir,
        sqlite_path=sqlite_path,
        media_dir=media_dir,
        logs_dir=logs_dir,
    )

    if security.owner_user_id not in security.allowed_user_ids:
        raise ConfigError(
            f"{_env_name('OWNER_USER_ID')} must also be present in {_env_name('ALLOWED_USER_IDS')}"
        )

    return AppConfig(
        feishu=feishu,
        codex=codex,
        storage=storage,
        security=security,
        logging=logging,
    )

