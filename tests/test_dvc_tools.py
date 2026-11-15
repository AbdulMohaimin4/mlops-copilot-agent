"""Tests for DVC tools: command allowlist, argument safety, parsing and unavailability."""

import json
import subprocess
from typing import Any

import pytest

from agent.schemas import ToolStatus
from agent.tools import dvc_tools
from agent.tools.common import ActionRejectedError, Tool, execute_tool


class FakeRun:
    """Stand-in for subprocess.run that records calls and returns canned stdout."""

    def __init__(self, stdout: str = "{}") -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.stdout = stdout

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=self.stdout, stderr="")


@pytest.mark.parametrize(
    ("program", "args"),
    [
        ("dvc", ["push"]),
        ("dvc", ["remote", "add", "evil", "s3://x"]),
        ("dvc", ["gc", "-w"]),
        ("git", ["tag", "data-v9"]),
        ("git", ["checkout", "main"]),
        ("rm", ["-rf", "/"]),
    ],
)
def test_non_allowlisted_commands_are_rejected_without_running(
    monkeypatch: pytest.MonkeyPatch, program: str, args: list[str]
) -> None:
    fake = FakeRun()
    monkeypatch.setattr(dvc_tools.subprocess, "run", fake)
    with pytest.raises(ActionRejectedError):
        dvc_tools._run_allowlisted(program, args)
    assert fake.calls == []


def test_commands_run_as_argv_list_without_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRun()
    monkeypatch.setattr(dvc_tools.subprocess, "run", fake)
    dvc_tools._run_allowlisted("dvc", ["status", "--json"])
    argv, kwargs = fake.calls[0]
    assert isinstance(argv, list) and argv[-2:] == ["status", "--json"]
    assert kwargs.get("shell", False) is False
    assert kwargs["timeout"] > 0


@pytest.mark.parametrize("rev", ["--upload-pack=touch /tmp/x", "data-v1; rm -rf /", "$(id)", ""])
def test_unsafe_revisions_are_invalid_requests(rev: str) -> None:
    tool = Tool.from_function(dvc_tools.reproduce_dataset_version)
    result = execute_tool(tool, {"rev": rev}, timeout_seconds=5)
    assert result.status is ToolStatus.ERROR
    assert result.error is not None and result.error.type == "invalid_request"


def test_check_data_changes_reports_modified_outputs(monkeypatch: pytest.MonkeyPatch) -> None:
    modified = {"prepare": [{"changed outs": {"data/breast_cancer.csv": "modified"}}]}
    monkeypatch.setattr(dvc_tools.subprocess, "run", FakeRun(json.dumps(modified)))
    data = dvc_tools.check_data_changes(dvc_tools.NoInput())
    assert data["workspace_clean"] is False
    assert data["workspace_changes"] == modified


def test_missing_dvc_binary_reports_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*_: Any, **__: Any) -> None:
        raise FileNotFoundError("dvc")

    monkeypatch.setattr(dvc_tools.subprocess, "run", missing)
    result = execute_tool(Tool.from_function(dvc_tools.check_data_changes), {}, timeout_seconds=5)
    assert result.status is ToolStatus.UNAVAILABLE
    assert result.data is None
