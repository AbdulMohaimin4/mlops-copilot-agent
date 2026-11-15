"""Tests for Kubernetes tools: namespace safety for writes, diagnosis parsing, unavailability."""

from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace as NS
from typing import Any

import pytest
from urllib3.exceptions import MaxRetryError

from agent.schemas import ToolStatus
from agent.tools import kubernetes_tools as k8s
from agent.tools.common import Tool, execute_tool


def fail_if_called() -> Any:
    raise AssertionError("the Kubernetes API must not be contacted")


def run(func: Any, args: dict[str, Any]) -> Any:
    return execute_tool(Tool.from_function(func), args, timeout_seconds=5)


@pytest.mark.parametrize("namespace", ["default", "kube-system", "production"])
def test_restart_outside_approved_namespace_is_rejected_before_any_api_call(
    monkeypatch: pytest.MonkeyPatch, namespace: str
) -> None:
    monkeypatch.setattr(k8s, "_api_client", fail_if_called)
    result = run(k8s.restart_deployment, {"namespace": namespace})
    assert result.status is ToolStatus.ERROR
    assert result.error is not None and result.error.type == "rejected"
    assert "not approved" in result.error.message


def test_restart_tool_is_flagged_state_changing() -> None:
    flags = {tool.name: tool.state_changing for tool in k8s.TOOLS}
    assert flags.pop("restart_deployment") is True
    assert not any(flags.values())


def test_malformed_names_are_invalid_requests() -> None:
    result = run(k8s.get_pod_status_and_logs, {"pod_name": "pod; kubectl delete ns mlops"})
    assert result.status is ToolStatus.ERROR
    assert result.error is not None and result.error.type == "invalid_request"


def make_pod(name: str, ready: bool, waiting: str | None = None, restarts: int = 0) -> Any:
    state = NS(waiting=NS(reason=waiting, message="back-off") if waiting else None, terminated=None)
    status = NS(
        name="inference-api",
        ready=ready,
        restart_count=restarts,
        image="mlops-inference:model-v6",
        state=state,
        last_state=None,
    )
    return NS(
        metadata=NS(name=name, creation_timestamp=datetime.now(UTC)),
        status=NS(phase="Pending" if waiting else "Running", container_statuses=[status]),
        spec=NS(node_name="mlops-control-plane"),
    )


class FakeCore:
    def __init__(self, pods: list[Any]) -> None:
        self.pods = pods

    def list_namespaced_pod(self, namespace: str, **_: Any) -> Any:
        return NS(items=self.pods)

    def list_namespaced_event(self, namespace: str, **_: Any) -> Any:
        event = NS(
            type="Warning",
            reason="Failed",
            message='Failed to pull image "mlops-inference:does-not-exist"',
            count=3,
            involved_object=NS(kind="Pod", name="bad-pod"),
            last_timestamp=datetime.now(UTC),
            event_time=None,
            metadata=NS(creation_timestamp=datetime.now(UTC)),
        )
        return NS(items=[event])


def test_unhealthy_pods_and_warning_events_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    pods = [
        make_pod("good-pod", ready=True),
        make_pod("bad-pod", ready=False, waiting="ImagePullBackOff"),
        make_pod("flaky-pod", ready=True, restarts=4),
    ]
    monkeypatch.setattr(k8s, "_api_client", lambda: nullcontext(None))
    monkeypatch.setattr(k8s.client, "CoreV1Api", lambda _: FakeCore(pods))
    data = run(k8s.find_unhealthy_pods, {}).data
    assert {p["name"] for p in data["unhealthy_pods"]} == {"bad-pod", "flaky-pod"}
    bad = next(p for p in data["unhealthy_pods"] if p["name"] == "bad-pod")
    assert bad["waiting_reasons"] == ["ImagePullBackOff"]
    assert "Failed to pull image" in data["warning_events"][0]["message"]


def test_unreachable_cluster_reports_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    class DownCore:
        def list_namespaced_pod(self, *_: Any, **__: Any) -> Any:
            raise MaxRetryError(None, "/api/v1/namespaces/mlops/pods", "connection refused")

    monkeypatch.setattr(k8s, "_api_client", lambda: nullcontext(None))
    monkeypatch.setattr(k8s.client, "CoreV1Api", lambda _: DownCore())
    result = run(k8s.find_unhealthy_pods, {})
    assert result.status is ToolStatus.UNAVAILABLE
    assert result.data is None
