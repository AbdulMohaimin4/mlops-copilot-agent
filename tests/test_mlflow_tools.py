"""Tests for MLflow tools using a fake MlflowClient: best-run reasoning and failure handling."""

from types import SimpleNamespace
from typing import Any

import pytest
from mlflow.exceptions import MlflowException

from agent.schemas import ToolStatus
from agent.tools import mlflow_tools
from agent.tools.common import Tool, execute_tool


def make_run(run_id: str, f1: float | None, status: str = "FINISHED", gate: str = "passed") -> Any:
    metrics = {"f1": f1, "test_rows": 114.0} if f1 is not None else {}
    return SimpleNamespace(
        info=SimpleNamespace(run_id=run_id, run_name=run_id, status=status, start_time=0),
        data=SimpleNamespace(
            metrics=metrics,
            params={"model_type": "logreg"},
            tags={"quality_gate": gate, "data_version": "data-v2", "git_dirty": "false"},
        ),
    )


class FakeClient:
    def __init__(self, runs: list[Any]) -> None:
        self.runs = runs

    def get_experiment_by_name(self, name: str) -> Any:
        return SimpleNamespace(experiment_id="1") if name != "missing" else None

    def search_experiments(self) -> list[Any]:
        return [SimpleNamespace(name="breast-cancer-classification")]

    def search_runs(self, *_: Any, **__: Any) -> list[Any]:
        return self.runs

    def search_model_versions(self, *_: Any) -> list[Any]:
        return []

    def get_run(self, run_id: str) -> Any:
        run = make_run(run_id, 0.97)
        run.info.experiment_id = "1"
        return run

    def list_artifacts(self, run_id: str) -> list[Any]:
        return []

    def search_logged_models(self, **_: Any) -> list[Any]:
        return [SimpleNamespace(model_id="m-1", name="model")]

    def list_logged_model_artifacts(self, model_id: str) -> list[Any]:
        return [SimpleNamespace(path="MLmodel"), SimpleNamespace(path="model.skops")]


def use_client(monkeypatch: pytest.MonkeyPatch, client: Any) -> None:
    monkeypatch.setattr(mlflow_tools, "_client", lambda: client)


def test_best_run_explains_choice_and_excludes_unfinished_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs = [
        make_run("a" * 32, 0.99, status="FAILED"),
        make_run("b" * 32, 0.96),
        make_run("c" * 32, 0.98),
        make_run("d" * 32, None),
    ]
    use_client(monkeypatch, FakeClient(runs))
    data = mlflow_tools.find_best_run(mlflow_tools.BestRunInput())
    assert data["best_run"]["run_id"] == "c" * 32
    assert data["rationale"]["margin_over_runner_up"] == 0.02
    excluded = {e["run_id"]: e["reason"] for e in data["excluded"]}
    assert excluded == {"a" * 32: "status FAILED", "d" * 32: "metric 'f1' not logged"}


def test_highest_f1_is_never_declared_production_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    use_client(monkeypatch, FakeClient([make_run("a" * 32, 0.99, gate="failed")]))
    readiness = mlflow_tools.find_best_run(mlflow_tools.BestRunInput())["production_readiness"]
    assert readiness["verdict"].startswith("not established")
    assert readiness["evidence"]["quality_gate"] == "failed"
    assert any("Kubernetes" in item for item in readiness["missing_evidence"])


def test_unreachable_server_reports_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    class DownClient(FakeClient):
        def search_experiments(self) -> list[Any]:
            raise MlflowException("API request to http://x failed with exception ConnectionError")

    use_client(monkeypatch, DownClient([]))
    result = execute_tool(Tool.from_function(mlflow_tools.list_experiments), {}, timeout_seconds=5)
    assert result.status is ToolStatus.UNAVAILABLE
    assert result.data is None


def test_unknown_experiment_lists_available_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    use_client(monkeypatch, FakeClient([]))
    tool = Tool.from_function(mlflow_tools.list_runs)
    result = execute_tool(tool, {"experiment_name": "missing"}, timeout_seconds=5)
    assert result.status is ToolStatus.ERROR
    assert result.error is not None and "breast-cancer-classification" in result.error.message


def test_malformed_run_id_is_invalid_request() -> None:
    tool = Tool.from_function(mlflow_tools.compare_runs)
    result = execute_tool(tool, {"run_ids": ["abc", "' OR 1=1"]}, timeout_seconds=5)
    assert result.status is ToolStatus.ERROR
    assert result.error is not None and result.error.type == "invalid_request"


def test_run_details_include_logged_model_artifacts(monkeypatch: pytest.MonkeyPatch) -> None:
    use_client(monkeypatch, FakeClient([]))
    data = mlflow_tools.get_run_details(mlflow_tools.RunInput(run_id="e" * 32))
    assert data["metrics"]["f1"] == 0.97
    assert data["params"] == {"model_type": "logreg"}
    assert data["logged_models"] == [
        {"model_id": "m-1", "name": "model", "files": ["MLmodel", "model.skops"]}
    ]
