"""Agent configuration, read only from environment variables with safe defaults."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Runtime settings. Secrets are read where they are used, never stored here."""

    log_level: str
    tool_timeout_seconds: float


def load_settings() -> Settings:
    """Build settings from the current environment."""
    return Settings(
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        tool_timeout_seconds=float(os.getenv("TOOL_TIMEOUT_SECONDS", "20")),
    )
