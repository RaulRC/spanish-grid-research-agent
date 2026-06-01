"""Spanish electricity market research agent.

A minimal but production-shaped agentic loop:
  - Spawns the spanish-grid-mcp server as a subprocess (stdio transport).
  - Discovers tools from the server and translates them to Anthropic schema.
  - Runs a streaming tool-use loop until Claude returns end_turn or hits the
    step ceiling.
  - Uses prompt caching on the system prompt + tool definitions so each
    iteration after the first is ~10% of full price.
  - Uses adaptive thinking with summarized display so reasoning shows up in
    the stream live.

Usage:
    python agent.py "Why were Spanish day-ahead prices high on 2026-05-20?"
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import sys
from datetime import date
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()

MODEL = os.getenv("AGENT_MODEL", "claude-sonnet-4-6")
MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "20"))
MCP_SERVER_CMD = shlex.split(
    os.getenv("MCP_SERVER_CMD", "python -m spanish_grid_mcp.server")
)
TODAY = date.today()
SYSTEM_PROMPT = (
    f"Today is {TODAY}.\n\n"
    + (Path(__file__).parent / "prompts" / "system.md").read_text()
)


def _extract_text(mcp_result) -> str:
    """Pull plain text out of an MCP CallToolResult (drops images/other types)."""
    parts = []
    for c in mcp_result.content:
        if getattr(c, "type", None) == "text":
            parts.append(c.text)
    return "\n".join(parts) if parts else json.dumps({"_empty": True})


async def run(question: str) -> None:
    server_params = StdioServerParameters(
        command=MCP_SERVER_CMD[0],
        args=MCP_SERVER_CMD[1:],
        env=os.environ.copy(),
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            mcp_tools = await session.list_tools()
            anthropic_tools = [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "input_schema": t.inputSchema,
                }
                for t in mcp_tools.tools
            ]
            print(
                f"[agent] Connected to MCP server. "
                f"Discovered {len(anthropic_tools)} tools.\n"
            )

            claude = Anthropic()
            messages: list[dict] = [{"role": "user", "content": question}]

            for step in range(1, MAX_STEPS + 1):
                print(f"\n--- step {step} ---")
                with claude.messages.stream(
                    model=MODEL,
                    max_tokens=16000,
                    system=[
                        {
                            "type": "text",
                            "text": SYSTEM_PROMPT,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    tools=anthropic_tools,
                    thinking={"type": "adaptive", "display": "summarized"},
                    messages=messages,
                ) as stream:
                    for text in stream.text_stream:
                        print(text, end="", flush=True)
                    response = stream.get_final_message()
                print()

                messages.append({"role": "assistant", "content": response.content})

                if response.stop_reason == "end_turn":
                    _print_usage(response)
                    return

                if response.stop_reason == "pause_turn":
                    continue

                if response.stop_reason == "tool_use":
                    tool_results = []
                    for block in response.content:
                        if block.type != "tool_use":
                            continue
                        print(f"[tool] {block.name}({json.dumps(block.input)})")
                        result = await session.call_tool(block.name, block.input)
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": _extract_text(result),
                                "is_error": bool(result.isError),
                            }
                        )
                    messages.append({"role": "user", "content": tool_results})
                    continue

                print(f"[agent] Unexpected stop_reason: {response.stop_reason}. Stopping.")
                return

            print(f"\n[agent] Reached step ceiling ({MAX_STEPS}) without end_turn.")


def _print_usage(response) -> None:
    u = response.usage
    print(
        f"\n[usage] input={u.input_tokens} output={u.output_tokens} "
        f"cache_read={u.cache_read_input_tokens} "
        f"cache_write={u.cache_creation_input_tokens}"
    )


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python agent.py '<your research question>'")
        print(
            'Example: python agent.py '
            '"Why were Spanish day-ahead prices high on 2026-05-20?"'
        )
        sys.exit(1)
    asyncio.run(run(" ".join(sys.argv[1:])))


if __name__ == "__main__":
    main()
