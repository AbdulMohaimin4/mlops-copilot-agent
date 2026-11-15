"""Pydantic models shared by every agent tool: the structured result and common inputs."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class ToolStatus(StrEnum):
    """Outcome of a tool call as reported to the LLM and the user."""

    OK = "ok"
    ERROR = "error"
    UNAVAILABLE = "unavailable"


class ToolError(BaseModel):
    """Machine-readable error details for a failed or unavailable tool call."""

    type: str
    message: str


class ToolResult(BaseModel):
    """Structured result returned by every tool."""

    tool: str
    status: ToolStatus
    data: dict[str, Any] | None = None
    error: ToolError | None = None
    duration_ms: float


class NoInput(BaseModel):
    """Input model for tools that take no arguments."""
