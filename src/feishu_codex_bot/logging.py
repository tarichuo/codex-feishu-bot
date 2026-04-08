"""Structured logging helpers for the Feishu Codex Bot."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
from typing import Any

from feishu_codex_bot.config import LoggingConfig


_STANDARD_RECORD_FIELDS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}

_SENSITIVE_FIELD_MARKERS = (
    "secret",
    "token",
    "password",
    "authorization",
    "credential",
    "cookie",
)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower()
    return any(marker in normalized for marker in _SENSITIVE_FIELD_MARKERS)


def _redact_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): ("***REDACTED***" if _is_sensitive_key(str(key)) else _redact_value(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_redact_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _record_extra(record: logging.LogRecord) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key in _STANDARD_RECORD_FIELDS or key.startswith("_"):
            continue
        if _is_sensitive_key(key):
            extra[key] = "***REDACTED***"
        else:
            extra[key] = _redact_value(value)
    return extra


class JsonLineFormatter(logging.Formatter):
    """Render log records as one-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", record.getMessage()),
            "message": record.getMessage(),
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        payload.update(_record_extra(record))
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), default=str)


class ContextLoggerAdapter(logging.LoggerAdapter):
    """Logger adapter that carries structured context fields."""

    def bind(self, **kwargs: Any) -> "ContextLoggerAdapter":
        merged = dict(self.extra)
        merged.update(kwargs)
        return ContextLoggerAdapter(self.logger, merged)

    def process(self, msg: Any, kwargs: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        extra = dict(self.extra)
        extra.update(kwargs.pop("extra", {}))
        kwargs["extra"] = extra
        return msg, kwargs


def _build_handler(handler: logging.Handler) -> logging.Handler:
    handler.setFormatter(JsonLineFormatter())
    return handler


def configure_logging(config: LoggingConfig, logs_dir: Path) -> ContextLoggerAdapter:
    """Configure application logging and return the root application logger."""
    logs_dir.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, config.level, logging.INFO))
    root_logger.handlers.clear()

    stream_handler = _build_handler(logging.StreamHandler(sys.stdout))
    file_handler = _build_handler(
        RotatingFileHandler(
            logs_dir / "app.log",
            maxBytes=1_048_576,
            backupCount=3,
            encoding="utf-8",
        )
    )

    root_logger.addHandler(stream_handler)
    root_logger.addHandler(file_handler)

    logging.captureWarnings(True)

    return get_logger("feishu_codex_bot")


def get_logger(name: str, **context: Any) -> ContextLoggerAdapter:
    """Create a structured logger adapter with optional default context."""
    return ContextLoggerAdapter(logging.getLogger(name), _redact_value(context))
