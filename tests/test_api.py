"""Tests for the agent's FastAPI interface: validation, sessions and confirmation flow."""

import pytest
from fastapi.testclient import TestClient

from agent.orchestrator import Orchestrator
from api import main as api
from tests.test_orchestrator import ScriptedLLM, call, model_turn, stub_tools, text


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    def scripted() -> Orchestrator:
        orch = Orchestrator(
            llm=ScriptedLLM(model_turn(call("trigger_dag")), text("Awaiting your confirmation."))
        )
        stub_tools(orch, {"trigger_dag": {"triggered": "run-1"}})
        return orch

    api.sessions.clear()
    monkeypatch.setattr(api, "new_orchestrator", scripted)
    return TestClient(api.app)


@pytest.mark.parametrize(
    "body",
    [{}, {"message": ""}, {"message": "hi", "session_id": "not-a-session"}, {"message": 3}],
)
def test_invalid_chat_requests_are_rejected(client: TestClient, body: dict[str, object]) -> None:
    assert client.post("/chat", json=body).status_code == 422


def test_chat_then_confirm_executes_pending_action_once(client: TestClient) -> None:
    first = client.post("/chat", json={"message": "retrain the model"}).json()
    action_id = first["pending_actions"][0]["action_id"]
    assert first["tool_calls"][0]["result"]["error"]["type"] == "confirmation_required"

    body = {"session_id": first["session_id"], "action_id": action_id}
    confirmed = client.post("/confirm", json=body)
    assert confirmed.status_code == 200
    assert confirmed.json()["tool_calls"][0]["result"]["status"] == "ok"
    assert client.post("/confirm", json=body).status_code == 404


def test_unknown_session_is_404(client: TestClient) -> None:
    response = client.post("/chat", json={"message": "hi", "session_id": "0" * 32})
    assert response.status_code == 404
