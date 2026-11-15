"""Tests for Airflow tools against a mocked Airflow 3 API (httpx.MockTransport)."""

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from agent.schemas import ToolStatus
from agent.tools import airflow_tools
from agent.tools.common import Tool, execute_tool

Handler = Callable[[httpx.Request], httpx.Response]


def mock_airflow(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> list[httpx.Request]:
    """Route every httpx.Client created by the tools through `handler`; return seen requests."""
    seen: list[httpx.Request] = []
    real_client = httpx.Client

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    def client(**kwargs: Any) -> httpx.Client:
        return real_client(transport=httpx.MockTransport(recording), **kwargs)

    monkeypatch.setattr(airflow_tools.httpx, "Client", client)
    monkeypatch.setenv("AIRFLOW_PASSWORD", "s3cret-pw")
    return seen


def token_then(routes: dict[str, Any]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/token":
            return httpx.Response(201, json={"access_token": "jwt-abc"})
        return httpx.Response(200, json=routes[request.url.path])

    return handler


def run(func: Any, args: dict[str, Any]) -> Any:
    return execute_tool(Tool.from_function(func), args, timeout_seconds=5)


def test_non_allowlisted_dag_is_rejected_without_calling_airflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = mock_airflow(monkeypatch, token_then({}))
    result = run(airflow_tools.trigger_dag, {"dag_id": "delete_everything"})
    assert result.status is ToolStatus.ERROR
    assert result.error is not None and result.error.type == "rejected"
    assert seen == []


def test_failed_tasks_found_in_latest_failed_run_with_bearer_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "manual__2026-11-15T10:00:00+00:00"
    base = f"/api/v2/dags/training_pipeline/dagRuns/{run_id}"
    tasks = [
        {"task_id": "validate_dataset", "state": "failed", "try_number": 1},
        {"task_id": "preprocess", "state": "upstream_failed", "try_number": 0},
    ]
    seen = mock_airflow(
        monkeypatch,
        token_then(
            {
                "/api/v2/dags/training_pipeline/dagRuns": {"dag_runs": [{"dag_run_id": run_id}]},
                base: {"dag_run_id": run_id, "state": "failed"},
                f"{base}/taskInstances": {"task_instances": tasks},
            }
        ),
    )
    result = run(airflow_tools.get_failed_tasks, {})
    assert result.status is ToolStatus.OK
    assert [t["task_id"] for t in result.data["failed_tasks"]] == ["validate_dataset", "preprocess"]
    api_calls = [r for r in seen if r.url.path.startswith("/api/v2")]
    assert all(r.headers["Authorization"] == "Bearer jwt-abc" for r in api_calls)


def test_rejected_credentials_are_an_error_without_leaking_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_airflow(monkeypatch, lambda _: httpx.Response(401, json={"detail": "bad"}))
    result = run(airflow_tools.list_dags, {})
    assert result.status is ToolStatus.ERROR
    assert result.error is not None and "s3cret-pw" not in result.error.message


def test_unreachable_airflow_reports_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    mock_airflow(monkeypatch, refuse)
    result = run(airflow_tools.get_dag_runs, {})
    assert result.status is ToolStatus.UNAVAILABLE
    assert result.data is None


def test_malformed_run_id_is_invalid_request() -> None:
    result = run(airflow_tools.get_task_logs, {"dag_run_id": "../../admin", "task_id": "x"})
    assert result.status is ToolStatus.ERROR
    assert result.error is not None and result.error.type == "invalid_request"


def test_task_log_includes_structured_exception_details(monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = "manual__2026-11-15T10:00:00+00:00"
    task = f"/api/v2/dags/training_pipeline/dagRuns/{run_id}/taskInstances/validate_dataset"
    failure = {
        "event": "Task failed with exception",
        "level": "error",
        "error_detail": [
            {"exc_type": "AirflowFailException", "exc_value": "data validation failed: 5 missing"}
        ],
    }
    mock_airflow(
        monkeypatch,
        token_then(
            {
                task: {"task_id": "validate_dataset", "state": "failed", "try_number": 1},
                f"{task}/logs/1": {"content": [{"event": "starting"}, failure]},
            }
        ),
    )
    result = run(airflow_tools.get_task_logs, {"dag_run_id": run_id, "task_id": "validate_dataset"})
    assert result.status is ToolStatus.OK
    assert result.data["error_lines"] == [
        "Task failed with exception | AirflowFailException: data validation failed: 5 missing"
    ]
