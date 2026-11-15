"""FastAPI interface to the MLOps agent: validates requests and returns evidence-backed answers.

Each session keeps its own conversation and pending actions. State-changing actions run
only through `POST /confirm` with the action ID the agent returned.
"""

import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agent.config import load_settings
from agent.logging_config import configure_logging
from agent.orchestrator import AgentResponse, Orchestrator

MAX_SESSIONS = 100


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging(load_settings().log_level)
    yield


app = FastAPI(title="MLOps Agent API", version="0.1.0", lifespan=lifespan)
sessions: OrderedDict[str, Orchestrator] = OrderedDict()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class ConfirmRequest(BaseModel):
    session_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    action_id: str = Field(pattern=r"^[0-9a-f]{8}$")


class ChatResponse(AgentResponse):
    session_id: str


def new_orchestrator() -> Orchestrator:
    """Create the agent for a new session (replaced in tests)."""
    return Orchestrator()


def get_session(session_id: str | None) -> tuple[str, Orchestrator]:
    if session_id is not None:
        if session_id not in sessions:
            raise HTTPException(status_code=404, detail="unknown session_id")
        sessions.move_to_end(session_id)
        return session_id, sessions[session_id]
    try:
        agent = new_orchestrator()
    except ValueError as exc:  # e.g. GEMINI_API_KEY missing
        raise HTTPException(status_code=503, detail=f"agent not configured: {exc}") from exc
    session_id = uuid.uuid4().hex
    sessions[session_id] = agent
    if len(sessions) > MAX_SESSIONS:
        sessions.popitem(last=False)
    return session_id, agent


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness of the agent API itself (not of the tools it calls)."""
    return {"status": "ok"}


@app.post("/chat")
def chat(request: ChatRequest) -> ChatResponse:
    """Send a message; the response includes the answer, tool trace and pending actions."""
    session_id, agent = get_session(request.session_id)
    response = agent.chat(request.message)
    return ChatResponse(session_id=session_id, **response.model_dump())


@app.post("/confirm")
def confirm(request: ConfirmRequest) -> ChatResponse:
    """Explicitly approve and execute a pending state-changing action."""
    session_id, agent = get_session(request.session_id)
    if request.action_id not in agent.pending:
        raise HTTPException(status_code=404, detail="unknown or already executed action_id")
    response = agent.confirm(request.action_id)
    return ChatResponse(session_id=session_id, **response.model_dump())
