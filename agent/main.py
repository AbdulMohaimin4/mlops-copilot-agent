"""Command-line chat with the MLOps agent.

Usage:
    uv run --env-file .env python -m agent.main [--save-transcript FILE] [-m MESSAGE ...]

Interactive commands: `/confirm <action_id>`, `/pending`, `/exit`.
"""

import argparse
import json
import sys
from pathlib import Path

from agent.config import load_settings
from agent.logging_config import configure_logging
from agent.orchestrator import AgentResponse, Orchestrator


def render_markdown(prompt: str, response: AgentResponse) -> str:
    """Render one exchange, including every tool call and its raw result, as Markdown."""
    lines = [f"### User\n\n{prompt}\n", f"### Tool calls (request `{response.request_id}`)\n"]
    for index, trace in enumerate(response.tool_calls, 1):
        result = trace.result
        lines.append(
            f"{index}. `{result.tool}({json.dumps(trace.args)})` → **{result.status.value}** "
            f"({result.duration_ms} ms)"
        )
        body = result.data if result.error is None else result.error.model_dump()
        lines.append(f"\n```json\n{json.dumps(body, indent=2, default=str)}\n```\n")
    if not response.tool_calls:
        lines.append("_none_\n")
    lines.append(f"### Agent\n\n{response.answer}\n")
    if response.pending_actions:
        pending = ", ".join(f"`{a.action_id}` ({a.tool})" for a in response.pending_actions)
        lines.append(f"_Pending actions awaiting confirmation: {pending}_\n")
    return "\n".join(lines)


def handle(agent: Orchestrator, message: str) -> AgentResponse | None:
    """Route a line of user input to chat or to a command."""
    if message.startswith("/confirm "):
        return agent.confirm(message.split(maxsplit=1)[1].strip())
    if message == "/pending":
        print(json.dumps([a.model_dump() for a in agent.pending.values()], indent=2))
        return None
    return agent.chat(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Chat with the MLOps agent.")
    parser.add_argument("-m", "--message", action="append", help="Send message(s) and exit.")
    parser.add_argument("--save-transcript", type=Path, help="Append exchanges as Markdown.")
    args = parser.parse_args()
    configure_logging(load_settings().log_level)
    agent = Orchestrator()

    def exchange(message: str) -> None:
        response = handle(agent, message)
        if response is None:
            return
        print(f"\n{response.answer}\n")
        tools = ", ".join(f"{t.result.tool}:{t.result.status.value}" for t in response.tool_calls)
        print(f"[request {response.request_id}] tools: {tools or 'none'}\n")
        if args.save_transcript:
            with args.save_transcript.open("a") as transcript:
                transcript.write(render_markdown(message, response) + "\n---\n\n")

    if args.message:
        for message in args.message:
            exchange(message)
        return 0
    print("MLOps agent. Commands: /confirm <id>, /pending, /exit")
    while True:
        try:
            message = input("> ").strip()
        except EOFError:
            break
        if message in ("/exit", "/quit"):
            break
        if message:
            exchange(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
