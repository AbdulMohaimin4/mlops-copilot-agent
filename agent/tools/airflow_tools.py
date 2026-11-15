"""Apache Airflow 3 workflow tools over the authenticated REST API (`/api/v2`).

Each call obtains a JWT from `/auth/token` using credentials from the environment.
Only DAGs in AIRFLOW_ALLOWED_DAGS can be triggered, and the orchestrator runs the
trigger only after the user explicitly confirms it.
"""

import os
from typing import Any, Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field

from agent.config import load_settings
from agent.logging_config import redact
from agent.schemas import NoInput
from agent.tools.common import ActionRejectedError, ServiceUnavailableError, Tool

DAG_ID_PATTERN = r"^[A-Za-z0-9_.\-]{1,250}$"
RUN_ID_PATTERN = r"^[A-Za-z0-9_.:+\-]{1,250}$"
FAILED_STATES = ("failed", "upstream_failed", "up_for_retry")
LOG_TAIL_LINES = 40


class DagInput(BaseModel):
    dag_id: str = Field(default="training_pipeline", pattern=DAG_ID_PATTERN)


class DagRunsInput(DagInput):
    limit: int = Field(default=5, ge=1, le=25, description="Most recent runs to return.")


class TriggerInput(DagInput):
    model_type: Literal["logreg", "rf"] = Field(default="logreg", description="Model to train.")


class FailedTasksInput(DagInput):
    dag_run_id: str | None = Field(
        default=None,
        pattern=RUN_ID_PATTERN,
        description="Run to inspect; latest failed run if omitted.",
    )


class TaskLogInput(DagInput):
    dag_run_id: str = Field(pattern=RUN_ID_PATTERN)
    task_id: str = Field(pattern=DAG_ID_PATTERN)
    try_number: int | None = Field(default=None, ge=1, description="Attempt; latest if omitted.")


def _request(method: str, path: str, headers: dict[str, str] | None = None, **kwargs: Any) -> Any:
    """Send an authenticated request to the Airflow API and return the parsed JSON body."""
    settings = load_settings()
    timeout = httpx.Timeout(settings.tool_timeout_seconds / 2)
    try:
        with httpx.Client(base_url=settings.airflow_url, timeout=timeout) as client:
            token = client.post(
                "/auth/token",
                json={
                    "username": settings.airflow_username,
                    "password": os.getenv("AIRFLOW_PASSWORD", ""),
                },
            )
            if token.status_code in (401, 403):
                raise PermissionError("Airflow rejected the configured credentials")
            token.raise_for_status()
            auth = {"Authorization": f"Bearer {token.json()['access_token']}"}
            response = client.request(
                method, f"/api/v2{path}", headers=auth | (headers or {}), **kwargs
            )
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        raise ServiceUnavailableError(f"Airflow API unreachable at {settings.airflow_url}") from exc
    if response.status_code == 404:
        raise LookupError(f"not found: {path}")
    response.raise_for_status()
    return response.json()


def _run_path(dag_id: str, dag_run_id: str) -> str:
    return f"/dags/{quote(dag_id, safe='')}/dagRuns/{quote(dag_run_id, safe='')}"


def _run_summary(run: dict[str, Any]) -> dict[str, Any]:
    keys = ("dag_run_id", "state", "run_type", "start_date", "end_date", "duration", "conf", "note")
    return {key: run.get(key) for key in keys}


def _task_summary(task: dict[str, Any]) -> dict[str, Any]:
    keys = ("task_id", "state", "try_number", "max_tries", "start_date", "end_date", "duration")
    return {key: task.get(key) for key in keys}


def _log_line(entry: Any) -> str:
    """Flatten an Airflow 3 structured log entry, including exception type and message,
    which Airflow stores in `error_detail` rather than in the event text."""
    if not isinstance(entry, dict):
        return str(entry)
    errors = [f"{e.get('exc_type')}: {e.get('exc_value')}" for e in entry.get("error_detail") or []]
    return " | ".join([str(entry.get("event", "")), *errors])


def list_dags(_: NoInput) -> dict[str, Any]:
    """List Airflow DAGs with paused state, tags, last run state, and whether the agent is
    allowed to trigger each one."""
    allowed = load_settings().airflow_allowed_dags
    body = _request("GET", "/dags", params={"limit": 100})
    return {
        "dags": [
            {
                "dag_id": dag["dag_id"],
                "is_paused": dag["is_paused"],
                "tags": [tag["name"] for tag in dag.get("tags", [])],
                "triggerable_by_agent": dag["dag_id"] in allowed,
            }
            for dag in body["dags"]
        ],
        "total": body["total_entries"],
    }


def get_dag_runs(args: DagRunsInput) -> dict[str, Any]:
    """Get the most recent runs of a DAG (newest first) with state, timing, conf and note."""
    body = _request(
        "GET",
        f"/dags/{quote(args.dag_id, safe='')}/dagRuns",
        params={"limit": args.limit, "order_by": "-start_date"},
    )
    return {"dag_id": args.dag_id, "runs": [_run_summary(run) for run in body["dag_runs"]]}


def trigger_dag(args: TriggerInput) -> dict[str, Any]:
    """Trigger a run of an allowlisted training DAG. State-changing: it only executes after the
    user explicitly confirms. Returns the new run ID and its initial state."""
    if args.dag_id not in load_settings().airflow_allowed_dags:
        raise ActionRejectedError(f"DAG '{args.dag_id}' is not in AIRFLOW_ALLOWED_DAGS")
    body = _request(
        "POST",
        f"/dags/{quote(args.dag_id, safe='')}/dagRuns",
        json={
            "logical_date": None,
            "conf": {"model_type": args.model_type},
            "note": "triggered by MLOps agent after explicit user confirmation",
        },
    )
    return {"dag_id": args.dag_id, "triggered": _run_summary(body)}


def get_failed_tasks(args: FailedTasksInput) -> dict[str, Any]:
    """Identify failed, upstream-failed or retrying tasks in a DAG run (latest failed run if no
    run ID is given), with attempt numbers and timing."""
    run_id = args.dag_run_id
    if run_id is None:
        failed = _request(
            "GET",
            f"/dags/{quote(args.dag_id, safe='')}/dagRuns",
            params={"state": "failed", "limit": 1, "order_by": "-start_date"},
        )["dag_runs"]
        if not failed:
            return {"dag_id": args.dag_id, "failed_run": None, "failed_tasks": []}
        run_id = failed[0]["dag_run_id"]
    run = _request("GET", _run_path(args.dag_id, run_id))
    tasks = _request("GET", f"{_run_path(args.dag_id, run_id)}/taskInstances")["task_instances"]
    return {
        "dag_id": args.dag_id,
        "run": _run_summary(run),
        "failed_tasks": [_task_summary(t) for t in tasks if t["state"] in FAILED_STATES],
        "all_tasks": [{"task_id": t["task_id"], "state": t["state"]} for t in tasks],
    }


def get_task_logs(args: TaskLogInput) -> dict[str, Any]:
    """Get execution details and the log tail of one task attempt, plus the error lines found in
    it, for failure diagnosis."""
    task_path = f"{_run_path(args.dag_id, args.dag_run_id)}/taskInstances/{quote(args.task_id)}"
    task = _request("GET", task_path)
    try_number = args.try_number or task["try_number"]
    body = _request("GET", f"{task_path}/logs/{try_number}", headers={"Accept": "application/json"})
    lines = [redact(_log_line(entry)) for entry in body.get("content", [])]
    error_markers = ("error", "exception", "traceback", "failed")
    errors = [line for line in lines if any(marker in line.lower() for marker in error_markers)]
    return {
        "task": _task_summary(task),
        "try_number": try_number,
        "error_lines": errors[-15:],
        "log_tail": lines[-LOG_TAIL_LINES:],
    }


TOOLS = [
    Tool.from_function(list_dags),
    Tool.from_function(get_dag_runs),
    Tool.from_function(trigger_dag, state_changing=True),
    Tool.from_function(get_failed_tasks),
    Tool.from_function(get_task_logs),
]
