"""Agent orchestrator: Gemini function-calling loop over the DVC, MLflow, Airflow and
Kubernetes tools.

Safety properties enforced in code, not only in the prompt:
- state-changing tools never run from an LLM call; they become pending actions that
  execute only when the user calls `confirm(action_id)`;
- every tool result is structured (ok / error / unavailable) and recorded in the trace;
- tools that were unavailable are listed in the response from the actual results, so
  the user sees them even if the model's text omits them.
"""

import json
import logging
import uuid
from typing import Any, Protocol

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

from agent import policy
from agent.config import Settings, load_settings
from agent.logging_config import redact, request_id_var
from agent.schemas import ToolError, ToolResult, ToolStatus
from agent.tools import airflow_tools, dvc_tools, kubernetes_tools, mlflow_tools
from agent.tools.common import Tool, execute_tool

logger = logging.getLogger("agent.orchestrator")

TOOL_GROUPS: dict[str, list[Tool]] = {
    "dvc": dvc_tools.TOOLS,
    "mlflow": mlflow_tools.TOOLS,
    "airflow": airflow_tools.TOOLS,
    "kubernetes": kubernetes_tools.TOOLS,
    "policy": policy.TOOLS,
}

SYSTEM_PROMPT = """You are an MLOps assistant with tools for DVC (dataset versions), MLflow
(experiments, runs, model registry), Airflow (training pipeline) and Kubernetes (the
inference API in namespace "mlops"), plus a retraining-policy tool.

Rules:
1. Every operational claim (versions, metrics, run states, pod health, hashes, IDs) must come
   from a tool result in this conversation. Never use prior knowledge or guess values.
2. If a tool returns status "unavailable" or "error", say so plainly, name the tool, and do not
   fill the gap with assumed results.
3. trigger_dag and restart_deployment change state. Call them only when the user explicitly asks
   for that action. Calling them does not execute anything: it creates a pending action that the
   user must approve with `/confirm <action_id>`. Tell the user the action ID.
4. Never start retraining as a side effect of a diagnostic or status question. To judge whether
   retraining is needed, first check recent training pipeline runs (get_dag_runs, and
   get_failed_tasks if any failed) and the data version, then call evaluate_retraining_need with
   that evidence and report its recommendation.
5. The run with the highest metric is not automatically production-ready; mention gate result,
   registry alias, data version and deployment status. Compare the deployed model version with the
   best run and the registry alias; any mismatch is an uncertainty or action, not a fact to hide.
6. For questions that need several tools, call all the tools you need, then answer with three
   sections: **Facts** (each with the tool it came from), **Uncertainties**, and
   **Recommended actions**.
Be concise. Prefer exact values (run IDs, versions, md5, pod names) over prose."""


class ToolCallTrace(BaseModel):
    """One tool call made during a request: arguments and structured result."""

    args: dict[str, Any]
    result: ToolResult


class PendingAction(BaseModel):
    """A state-changing tool call waiting for explicit user confirmation."""

    action_id: str
    tool: str
    args: dict[str, Any]


class AgentResponse(BaseModel):
    """The agent's answer plus the evidence it was built from."""

    request_id: str
    answer: str
    tool_calls: list[ToolCallTrace]
    unavailable_tools: list[str]
    pending_actions: list[PendingAction]


class LLM(Protocol):
    """Anything that turns a conversation into the model's next turn."""

    def generate(self, history: list[types.Content]) -> types.GenerateContentResponse: ...


class GeminiLLM:
    """Gemini client configured with the system prompt and tool declarations."""

    def __init__(self, tools: list[Tool], settings: Settings) -> None:
        self._client = genai.Client(
            http_options=types.HttpOptions(timeout=int(settings.llm_timeout_seconds * 1000))
        )
        self._model = settings.agent_model
        self._config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[types.Tool(function_declarations=[declare(tool) for tool in tools])],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    def generate(self, history: list[types.Content]) -> types.GenerateContentResponse:
        return self._client.models.generate_content(
            model=self._model, contents=history, config=self._config
        )


def all_tools() -> list[Tool]:
    """Every tool the LLM may select, across all modules."""
    return [tool for group in TOOL_GROUPS.values() for tool in group]


def declare(tool: Tool) -> types.FunctionDeclaration:
    """Expose a tool to Gemini: name, docstring and the JSON schema of its Pydantic input."""
    return types.FunctionDeclaration(
        name=tool.name,
        description=tool.description,
        parameters_json_schema=tool.input_model.model_json_schema(),
    )


class Orchestrator:
    """One conversation with the agent: history, tool dispatch and pending actions."""

    def __init__(self, llm: LLM | None = None, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self.tools = {tool.name: tool for tool in all_tools()}
        self.llm = llm or GeminiLLM(list(self.tools.values()), self.settings)
        self.history: list[types.Content] = []
        self.pending: dict[str, PendingAction] = {}

    def chat(self, message: str) -> AgentResponse:
        """Answer a user message, calling tools as the model requests."""
        request_id = uuid.uuid4().hex[:12]
        token = request_id_var.set(request_id)
        try:
            return self._chat(request_id, message)
        finally:
            request_id_var.reset(token)

    def confirm(self, action_id: str) -> AgentResponse:
        """Execute a pending state-changing action that the user has explicitly approved."""
        request_id = uuid.uuid4().hex[:12]
        token = request_id_var.set(request_id)
        try:
            action = self.pending.pop(action_id, None)
            if action is None:
                return self._response(request_id, f"No pending action '{action_id}'.", [])
            logger.info("user confirmed action", extra={"event": "confirm", "tool": action.tool})
            result = self._execute(action.tool, action.args)
            trace = [ToolCallTrace(args=action.args, result=result)]
            self.history.append(
                _user_text(f"I confirmed action {action_id}; result: {_dump(result)}")
            )
            answer = (
                f"Executed confirmed action {action_id} ({action.tool}): {result.status.value}."
            )
            return self._response(request_id, answer, trace)
        finally:
            request_id_var.reset(token)

    def _chat(self, request_id: str, message: str) -> AgentResponse:
        turn_start = len(self.history)
        self.history.append(_user_text(message))
        traces: list[ToolCallTrace] = []
        for _ in range(self.settings.max_tool_rounds):
            try:
                reply = self.llm.generate(self.history)
            except (genai_errors.APIError, httpx.HTTPError) as exc:
                # Drop this turn so the conversation stays valid for a retry.
                del self.history[turn_start:]
                logger.error(
                    "llm call failed",
                    extra={"event": "llm_error", "error_type": type(exc).__name__},
                )
                answer = (
                    f"The language model is unavailable ({redact(str(exc))[:200]}). No answer was "
                    "generated; the tool calls made before the failure are listed in the trace."
                )
                return self._response(request_id, answer, traces)
            content = reply.candidates[0].content if reply.candidates else None
            if content is None:
                return self._response(request_id, "The model returned no answer.", traces)
            self.history.append(content)
            calls = reply.function_calls or []
            if not calls:
                return self._response(request_id, reply.text or "", traces)
            parts = []
            for call in calls:
                args = dict(call.args or {})
                result = self._dispatch(call.name or "", args)
                traces.append(ToolCallTrace(args=args, result=result))
                parts.append(_function_response(call, result))
            self.history.append(types.Content(role="user", parts=parts))
        answer = (
            f"Stopped after {self.settings.max_tool_rounds} tool rounds without a final answer."
        )
        return self._response(request_id, answer, traces)

    def _dispatch(self, name: str, args: dict[str, Any]) -> ToolResult:
        tool = self.tools.get(name)
        if tool is not None and tool.state_changing:
            return self._hold_for_confirmation(tool, args)
        return self._execute(name, args)

    def _execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        tool = self.tools.get(name)
        if tool is None:
            error = ToolError(type="unknown_tool", message=f"no tool named '{name}'")
            return ToolResult(tool=name, status=ToolStatus.ERROR, error=error, duration_ms=0)
        return execute_tool(tool, args, self.settings.tool_timeout_seconds)

    def _hold_for_confirmation(self, tool: Tool, args: dict[str, Any]) -> ToolResult:
        try:
            tool.input_model.model_validate(args)
        except ValueError as exc:
            error = ToolError(type="invalid_request", message=str(exc))
            return ToolResult(tool=tool.name, status=ToolStatus.ERROR, error=error, duration_ms=0)
        action = PendingAction(action_id=uuid.uuid4().hex[:8], tool=tool.name, args=args)
        self.pending[action.action_id] = action
        logger.info("action held", extra={"event": "pending_action", "tool": tool.name})
        message = (
            f"Not executed. State-changing action '{tool.name}' is pending user confirmation; "
            f"the user must reply `/confirm {action.action_id}`."
        )
        return ToolResult(
            tool=tool.name,
            status=ToolStatus.ERROR,
            data={"action_id": action.action_id},
            error=ToolError(type="confirmation_required", message=message),
            duration_ms=0,
        )

    def _response(self, request_id: str, answer: str, traces: list[ToolCallTrace]) -> AgentResponse:
        unavailable = sorted(
            {t.result.tool for t in traces if t.result.status is ToolStatus.UNAVAILABLE}
        )
        if unavailable:
            answer += (
                f"\n\n[Unavailable during this request: {', '.join(unavailable)}. "
                "No results were assumed for these tools.]"
            )
        return AgentResponse(
            request_id=request_id,
            answer=answer,
            tool_calls=traces,
            unavailable_tools=unavailable,
            pending_actions=list(self.pending.values()),
        )


def _user_text(text: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part(text=text)])


def _dump(result: ToolResult) -> str:
    return json.dumps(result.model_dump(mode="json"), default=str)


def _function_response(call: types.FunctionCall, result: ToolResult) -> types.Part:
    response = types.FunctionResponse(
        id=call.id, name=call.name, response=json.loads(_dump(result))
    )
    return types.Part(function_response=response)
