"""Pydantic AI agent for the Spanish electricity market.

Parallel implementation to agent.py using the Pydantic AI framework
instead of a hand-rolled Claude loop.

Usage:
    python agent_pydantic.py "Why were Spanish day-ahead prices high on 2026-05-20?"
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import sys
from collections.abc import AsyncIterable
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from pydantic_ai import (
    Agent,
    AgentStreamEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    RunContext,
)
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from pydantic_ai.usage import UsageLimits

load_dotenv()

MODEL = os.getenv("AGENT_MODEL", "claude-opus-4-7")
MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "20"))
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL")
MCP_SERVER_CMD = (
    shlex.split(os.environ["MCP_SERVER_CMD"])
    if "MCP_SERVER_CMD" in os.environ
    else [sys.executable, "-m", "spanish_grid_mcp.server"]
)
TODAY = date.today()
SYSTEM_PROMPT = (
    f"Today is {TODAY}.\n\n"
    + (Path(__file__).parent / "prompts" / "system.md").read_text()
)


async def event_stream_handler(
    ctx: RunContext,
    event_stream: AsyncIterable[AgentStreamEvent],
) -> None:
    """Live-stream tool calls as they happen."""
    async for event in event_stream:
        if isinstance(event, FunctionToolCallEvent):
            print(
                f"\n[tool] {event.part.tool_name}({json.dumps(event.part.args)})"
            )
        elif isinstance(event, FunctionToolResultEvent):
            result = event.part.content
            if len(result) > 200:
                result = result[:200] + "..."
            print(f"[tool] → {result}")


async def run(question: str) -> None:
    if MCP_SERVER_URL:
        print(f"[agent] Connecting to MCP server at {MCP_SERVER_URL}")
        mcp_server = MCPToolset(MCP_SERVER_URL)
    else:
        from fastmcp.client import Client
        from fastmcp.client.transports import StdioTransport

        transport = StdioTransport(
            command=MCP_SERVER_CMD[0],
            args=MCP_SERVER_CMD[1:],
            env=os.environ.copy(),
        )
        mcp_server = MCPToolset(Client(transport, timeout=120))

    model = AnthropicModel(MODEL)
    agent = Agent(
        model,
        system_prompt=SYSTEM_PROMPT,
        toolsets=[mcp_server],
        model_settings=AnthropicModelSettings(
            anthropic_thinking={"type": "adaptive"},
            anthropic_effort="high",
            anthropic_cache=True,
            anthropic_cache_instructions=True,
        ),
    )

    print(f"[agent] Connected to MCP server. Starting research.\n")

    async with agent:
        async with agent.run_stream(
            question,
            event_stream_handler=event_stream_handler,
            usage_limits=UsageLimits(request_limit=MAX_STEPS),
        ) as result:
            async for chunk in result.stream_text(delta=True):
                print(chunk, end="", flush=True)
            print()
            u = result.usage

    print(
        f"\n[usage] input={u.input_tokens} output={u.output_tokens} "
        f"cache_read={u.cache_read_tokens} cache_write={u.cache_write_tokens}"
    )


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python agent_pydantic.py '<your research question>'")
        print(
            "Example: python agent_pydantic.py "
            '"Why were Spanish day-ahead prices high on 2026-05-20?"'
        )
        sys.exit(1)
    asyncio.run(run(" ".join(sys.argv[1:])))


if __name__ == "__main__":
    main()
