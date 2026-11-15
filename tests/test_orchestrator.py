"""Orchestrator tests with a scripted LLM (real google-genai types, no network)."""

from typing import Any

import pytest
from google.genai import errors, types

from agent.orchestrator import TOOL_GROUPS, Orchestrator, all_tools, declare
from agent.schemas import ToolStatus
from agent.tools.common import ServiceUnavailableError, Tool


def model_turn(*parts: types.Part) -> types.GenerateContentResponse:
    content = types.Content(role="model", parts=list(parts))
    return types.GenerateContentResponse(candidates=[types.Candidate(content=content)])


def call(name: str, args: dict[str, Any] | None = None) -> types.Part:
    return types.Part(function_call=types.FunctionCall(id=f"id-{name}", name=name, args=args or {}))


def text(answer: str) -> types.GenerateContentResponse:
    return model_turn(types.Part(text=answer))


class ScriptedLLM:
    """Returns pre-written model turns in order and records what it was shown."""

    def __init__(self, *turns: types.GenerateContentResponse) -> None:
        self.turns = list(turns)
        self.seen: list[list[types.Content]] = []

    def generate(self, history: list[types.Content]) -> types.GenerateContentResponse:
        self.seen.append(list(history))
        return self.turns.pop(0)


def stub_tools(orch: Orchestrator, outputs: dict[str, Any]) -> list[str]:
    """Replace every tool's implementation with a stub; return the log of executed tools."""
    executed: list[str] = []

    def make(name: str) -> Any:
        def stub(_: Any) -> dict[str, Any]:
            executed.append(name)
            output = outputs.get(name, {"stub": name})
            if isinstance(output, Exception):
                raise output
            return dict(output)

        return stub

    for name, tool in orch.tools.items():
        orch.tools[name] = Tool(
            name, tool.description, tool.input_model, make(name), tool.state_changing
        )
    return executed


def orchestrator(*turns: types.GenerateContentResponse) -> Orchestrator:
    return Orchestrator(llm=ScriptedLLM(*turns))


def test_each_module_exposes_at_least_three_valid_tools() -> None:
    for group in ("dvc", "mlflow", "airflow", "kubernetes"):
        assert len(TOOL_GROUPS[group]) >= 3, group
    names = [tool.name for tool in all_tools()]
    assert len(names) == len(set(names))
    for tool in all_tools():
        declaration = declare(tool)
        assert declaration.description, f"{tool.name} needs a docstring for the LLM"
        assert declaration.parameters_json_schema["type"] == "object"


def test_selected_tool_is_executed_and_result_returned_to_model() -> None:
    orch = orchestrator(model_turn(call("list_runs", {"max_results": 3})), text("Two runs."))
    executed = stub_tools(orch, {"list_runs": {"runs": ["a", "b"]}})
    response = orch.chat("List recent runs")
    assert executed == ["list_runs"]
    assert response.answer == "Two runs."
    assert response.tool_calls[0].args == {"max_results": 3}
    assert response.tool_calls[0].result.status is ToolStatus.OK
    returned = orch.history[-2].parts[0].function_response  # type: ignore[index]
    assert returned is not None and returned.id == "id-list_runs"


def test_state_changing_call_waits_for_explicit_confirmation() -> None:
    orch = orchestrator(model_turn(call("trigger_dag", {"model_type": "rf"})), text("Pending."))
    executed = stub_tools(orch, {"trigger_dag": {"triggered": "run-1"}})
    response = orch.chat("Retrain with random forest")
    assert executed == []
    assert response.tool_calls[0].result.error is not None
    assert response.tool_calls[0].result.error.type == "confirmation_required"
    action_id = response.pending_actions[0].action_id

    confirmed = orch.confirm(action_id)
    assert executed == ["trigger_dag"]
    assert confirmed.tool_calls[0].result.status is ToolStatus.OK
    assert orch.confirm(action_id).answer.startswith("No pending action")
    assert executed == ["trigger_dag"]


def test_invalid_state_changing_request_is_not_queued() -> None:
    bad = call("restart_deployment", {"namespace": "Not A Namespace!"})
    orch = orchestrator(model_turn(bad), text("Rejected."))
    stub_tools(orch, {})
    response = orch.chat("restart it")
    assert response.tool_calls[0].result.error is not None
    assert response.tool_calls[0].result.error.type == "invalid_request"
    assert response.pending_actions == []


def test_unavailable_tool_is_surfaced_even_if_model_text_omits_it() -> None:
    orch = orchestrator(model_turn(call("find_unhealthy_pods")), text("Everything looks healthy."))
    stub_tools(orch, {"find_unhealthy_pods": ServiceUnavailableError("cluster unreachable")})
    response = orch.chat("Is the API healthy?")
    result = response.tool_calls[0].result
    assert result.status is ToolStatus.UNAVAILABLE and result.data is None
    assert response.unavailable_tools == ["find_unhealthy_pods"]
    assert "Unavailable during this request: find_unhealthy_pods" in response.answer
    sent = orch.history[-2].parts[0].function_response  # type: ignore[index]
    assert sent is not None and sent.response is not None
    assert sent.response["status"] == "unavailable"


def test_integration_scenario_uses_all_four_systems_without_retraining() -> None:
    scenario = [
        call("find_best_run"),
        call("get_run_details", {"run_id": "a" * 32}),
        call("get_current_dataset_version"),
        call("get_deployed_version"),
        call("find_unhealthy_pods"),
        call("get_dag_runs"),
    ]
    policy_call = call(
        "evaluate_retraining_need",
        {"latest_quality_gate": "passed", "model_data_version": "data-v2"},
    )
    final = "**Facts**\n...\n**Uncertainties**\n...\n**Recommended actions**\n..."
    orch = orchestrator(model_turn(*scenario), model_turn(policy_call), text(final))
    executed = stub_tools(orch, {})
    response = orch.chat(
        "Find the best recent model, identify the dataset version used to train it, check the "
        "deployment status, and explain whether retraining is necessary."
    )
    groups = {g for g, tools in TOOL_GROUPS.items() for t in tools if t.name in executed}
    assert {"dvc", "mlflow", "airflow", "kubernetes"} <= groups
    assert "trigger_dag" not in executed and "restart_deployment" not in executed
    assert response.pending_actions == []
    assert response.answer == final


def test_unknown_tool_and_round_limit_are_handled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_TOOL_ROUNDS", "2")
    orch = orchestrator(model_turn(call("rm_rf")), model_turn(call("rm_rf")))
    stub_tools(orch, {})
    response = orch.chat("do something")
    assert all(
        t.result.error and t.result.error.type == "unknown_tool" for t in response.tool_calls
    )
    assert response.answer.startswith("Stopped after 2 tool rounds")


def test_llm_failure_returns_honest_answer_and_keeps_history_valid() -> None:
    class QuotaExceededLLM:
        def generate(self, history: list[types.Content]) -> types.GenerateContentResponse:
            raise errors.ClientError(429, {"error": {"message": "RESOURCE_EXHAUSTED"}})

    orch = Orchestrator(llm=QuotaExceededLLM())
    response = orch.chat("Is the API healthy?")
    assert response.answer.startswith("The language model is unavailable")
    assert response.tool_calls == []
    assert orch.history == []
