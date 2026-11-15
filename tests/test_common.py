"""Tests for the shared tool execution path: validation, timeouts, failures and structured logs."""

import json
import logging
import time
from typing import Any

import pytest
from pydantic import BaseModel

from agent.logging_config import REDACTED, JsonFormatter, request_id_var
from agent.schemas import ToolStatus
from agent.tools.common import (
    ActionRejectedError,
    ServiceUnavailableError,
    Tool,
    execute_tool,
)


class EchoInput(BaseModel):
    value: int


def echo(args: EchoInput) -> dict[str, Any]:
    """Return the value."""
    return {"value": args.value}


def slow(args: EchoInput) -> dict[str, Any]:
    """Sleep longer than the timeout."""
    time.sleep(1)
    return {}


def down(args: EchoInput) -> dict[str, Any]:
    """Simulate an unreachable service."""
    raise ServiceUnavailableError("connection refused")


def forbidden(args: EchoInput) -> dict[str, Any]:
    """Simulate a safety rejection."""
    raise ActionRejectedError("namespace not approved")


def test_successful_call_returns_ok_with_data() -> None:
    result = execute_tool(Tool.from_function(echo), {"value": 3}, timeout_seconds=1)
    assert result.status is ToolStatus.OK
    assert result.data == {"value": 3}
    assert result.error is None


def test_invalid_request_is_rejected_before_execution() -> None:
    result = execute_tool(Tool.from_function(echo), {"value": "not-a-number"}, timeout_seconds=1)
    assert result.status is ToolStatus.ERROR
    assert result.error is not None and result.error.type == "invalid_request"
    assert result.data is None


def test_timeout_reports_unavailable_without_data() -> None:
    result = execute_tool(Tool.from_function(slow), {"value": 1}, timeout_seconds=0.05)
    assert result.status is ToolStatus.UNAVAILABLE
    assert result.error is not None and result.error.type == "timeout"
    assert result.data is None


@pytest.mark.parametrize(
    ("func", "status", "error_type"),
    [
        (down, ToolStatus.UNAVAILABLE, "service_unavailable"),
        (forbidden, ToolStatus.ERROR, "rejected"),
    ],
)
def test_failures_map_to_structured_status(func: Any, status: ToolStatus, error_type: str) -> None:
    result = execute_tool(Tool.from_function(func), {"value": 1}, timeout_seconds=1)
    assert result.status is status
    assert result.error is not None and result.error.type == error_type


def test_structured_log_has_required_fields_and_no_secrets(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "sk-test-secret-123"
    monkeypatch.setenv("GEMINI_API_KEY", secret)

    def leaky(args: EchoInput) -> dict[str, Any]:
        """Fail with a message containing a secret."""
        raise RuntimeError(f"auth failed for key {secret}")

    token = request_id_var.set("req-42")
    try:
        with caplog.at_level(logging.INFO, logger="agent.tools"):
            result = execute_tool(Tool.from_function(leaky), {"value": 1}, timeout_seconds=1)
            logging.getLogger("agent.tools").info("calling with Bearer abc.def and %s", secret)
        line = JsonFormatter().format(caplog.records[-2])
        leaky_message_line = JsonFormatter().format(caplog.records[-1])
    finally:
        request_id_var.reset(token)

    entry = json.loads(line)
    assert entry["tool"] == "leaky"
    assert entry["status"] == "error"
    assert isinstance(entry["duration_ms"], float)
    assert entry["request_id"] == "req-42"
    assert result.error is not None
    assert secret not in result.error.message and REDACTED in result.error.message
    assert secret not in leaky_message_line and "abc.def" not in leaky_message_line
