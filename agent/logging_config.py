"""Structured JSON logging with per-request IDs and secret redaction."""

import json
import logging
import os
import re
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

SECRET_ENV_VARS = ("GEMINI_API_KEY", "AIRFLOW_PASSWORD")
_EXTRA_FIELDS = ("tool", "status", "duration_ms", "error_type", "event")
_BEARER_TOKEN = re.compile(r"Bearer\s+[A-Za-z0-9._\-]+")
REDACTED = "***REDACTED***"


def redact(text: str) -> str:
    """Remove known secret values and bearer tokens from a string."""
    for name in SECRET_ENV_VARS:
        value = os.getenv(name)
        if value:
            text = text.replace(value, REDACTED)
    return _BEARER_TOKEN.sub(f"Bearer {REDACTED}", text)


class JsonFormatter(logging.Formatter):
    """Render each record as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": request_id_var.get(),
            "message": record.getMessage(),
        }
        for field in _EXTRA_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        return redact(json.dumps(payload, default=str))


def configure_logging(level: str = "INFO") -> None:
    """Send JSON logs to stderr for the whole process."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
