"""Tool definition and the single execution path every tool call goes through.

`execute_tool` validates input, enforces a timeout, maps failures to a structured
`ToolResult`, and writes one structured log line per call.
"""

import inspect
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from agent.logging_config import redact
from agent.schemas import ToolError, ToolResult, ToolStatus

logger = logging.getLogger("agent.tools")

# Shared pool so a timed-out call does not block the caller while its thread finishes.
_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="tool")


class ServiceUnavailableError(Exception):
    """The external service (DVC, MLflow, Airflow, Kubernetes) could not be reached."""


class ActionRejectedError(Exception):
    """A safety policy rejected the requested operation."""


ToolFunc = Callable[[Any], dict[str, Any]]


@dataclass(frozen=True)
class Tool:
    """An LLM-callable tool: its name, docstring, Pydantic input model and implementation."""

    name: str
    description: str
    input_model: type[BaseModel]
    func: ToolFunc
    state_changing: bool = False

    @classmethod
    def from_function(cls, func: ToolFunc, *, state_changing: bool = False) -> "Tool":
        """Build a tool from a function whose single parameter is annotated with its input model."""
        param = next(iter(inspect.signature(func).parameters.values()))
        return cls(
            name=func.__name__,
            description=inspect.getdoc(func) or "",
            input_model=param.annotation,
            func=func,
            state_changing=state_changing,
        )


def execute_tool(tool: Tool, raw_args: dict[str, Any], timeout_seconds: float) -> ToolResult:
    """Run a tool safely and return a structured result; never raises."""
    start = time.perf_counter()
    try:
        args = tool.input_model.model_validate(raw_args)
        data = _executor.submit(tool.func, args).result(timeout=timeout_seconds)
        result = _result(tool.name, ToolStatus.OK, start, data=data)
    except ValidationError as exc:
        result = _result(tool.name, ToolStatus.ERROR, start, "invalid_request", str(exc))
    except TimeoutError:
        message = f"no response within {timeout_seconds}s"
        result = _result(tool.name, ToolStatus.UNAVAILABLE, start, "timeout", message)
    except ServiceUnavailableError as exc:
        result = _result(tool.name, ToolStatus.UNAVAILABLE, start, "service_unavailable", str(exc))
    except ActionRejectedError as exc:
        result = _result(tool.name, ToolStatus.ERROR, start, "rejected", str(exc))
    except Exception as exc:  # any other tool failure becomes a structured error, not a crash
        result = _result(tool.name, ToolStatus.ERROR, start, type(exc).__name__, str(exc))
    _log(result)
    return result


def _result(
    name: str,
    status: ToolStatus,
    start: float,
    error_type: str | None = None,
    message: str = "",
    data: dict[str, Any] | None = None,
) -> ToolResult:
    error = ToolError(type=error_type, message=redact(message)) if error_type else None
    duration_ms = round((time.perf_counter() - start) * 1000, 1)
    return ToolResult(tool=name, status=status, data=data, error=error, duration_ms=duration_ms)


def _log(result: ToolResult) -> None:
    logger.info(
        "tool call finished",
        extra={
            "event": "tool_call",
            "tool": result.tool,
            "status": result.status.value,
            "duration_ms": result.duration_ms,
            "error_type": result.error.type if result.error else None,
        },
    )
