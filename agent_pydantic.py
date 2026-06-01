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
from collections.abc import AsyncIterator
from dataclasses import dataclass
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
MAX_TOKENS = int(os.getenv("AGENT_MAX_TOKENS", "8192"))
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


# --- Event types ------------------------------------------------------------


@dataclass
class ToolCallEvent:
    name: str
    args: str


@dataclass
class ToolResultEvent:
    content_preview: str


@dataclass
class TextDeltaEvent:
    content: str


@dataclass
class UsageEvent:
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_write: int


@dataclass
class LogEvent:
    message: str


AgentEvent = ToolCallEvent | ToolResultEvent | TextDeltaEvent | UsageEvent | LogEvent


# --- Core agent loop --------------------------------------------------------


async def run_agent(question: str) -> AsyncIterator[AgentEvent]:
    """Run the research agent, yielding typed events for consumers (CLI, Streamlit, etc.)."""
    if MCP_SERVER_URL:
        yield LogEvent(f"Connecting to MCP server at {MCP_SERVER_URL}")
        mcp_server = MCPToolset(MCP_SERVER_URL)
    else:
        yield LogEvent("Starting MCP server subprocess")
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
            max_tokens=MAX_TOKENS,
            anthropic_thinking={"type": "adaptive"},
            anthropic_effort="high",
            anthropic_cache=True,
            anthropic_cache_instructions=True,
        ),
    )

    yield LogEvent("Connected to MCP server. Starting research.")

    async with agent:
        queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

        async def event_handler(
            ctx: RunContext,
            event_stream: AsyncIterable[AgentStreamEvent],
        ) -> None:
            async for event in event_stream:
                if isinstance(event, FunctionToolCallEvent):
                    await queue.put(
                        ToolCallEvent(event.part.tool_name, json.dumps(event.part.args))
                    )
                elif isinstance(event, FunctionToolResultEvent):
                    preview = event.part.content
                    if len(preview) > 200:
                        preview = preview[:200] + "..."
                    await queue.put(ToolResultEvent(preview))

        async with agent.run_stream(
            question,
            event_stream_handler=event_handler,
            usage_limits=UsageLimits(request_limit=MAX_STEPS),
        ) as result:
            async def stream_text_to_queue() -> None:
                async for chunk in result.stream_text(delta=True):
                    await queue.put(TextDeltaEvent(chunk))
                await queue.put(None)

            text_task = asyncio.create_task(stream_text_to_queue())

            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event

            await text_task
            usage = result.usage
            yield UsageEvent(
                usage.input_tokens,
                usage.output_tokens,
                usage.cache_read_tokens,
                usage.cache_write_tokens,
            )


# --- CLI entry point --------------------------------------------------------


async def run(question: str) -> None:
    async for event in run_agent(question):
        if isinstance(event, LogEvent):
            print(event.message)
        elif isinstance(event, ToolCallEvent):
            print(f"\n[tool] {event.name}({event.args})")
        elif isinstance(event, ToolResultEvent):
            print(f"[tool] → {event.content_preview}")
        elif isinstance(event, TextDeltaEvent):
            print(event.content, end="", flush=True)
        elif isinstance(event, UsageEvent):
            print(
                f"\n[usage] input={event.input_tokens} output={event.output_tokens} "
                f"cache_read={event.cache_read} cache_write={event.cache_write}"
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
