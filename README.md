# spanish-grid-research-agent

A "deep research" agent for the Spanish electricity market. Ask it a question in plain language; it autonomously decides which Spanish grid data to fetch, calls the [`spanish-grid-mcp`](https://github.com/raulrc/spanish-grid-mcp) server over MCP, iterates as needed, and produces a written analysis with sources.

Two implementations live side by side in this repo:

- **`agent.py`** — Hand-rolled Claude SDK loop (~150 lines). Deliberately framework-free, showing the raw tool-use pattern.
- **`agent_pydantic.py`** — Built on [Pydantic AI](https://ai.pydantic.dev/). Uses `MCPToolset` for MCP connectivity and `stream_text` for live output.

Examples of questions it's built for:

- *"Why were Spanish day-ahead prices high on 2026-05-20?"*
- *"How did the wind generation collapse on 2025-11-12 affect prices?"*
- *"Compare PVPC and wholesale prices for the last 30 days — when is PVPC most divergent?"*

## How it works

```
Your question
   │
   ├── agent.py (stdio MCP)
   │   └── spawns spanish-grid-mcp as subprocess → ESIOS / REE / AEMET
   │
   └── agent_pydantic.py (stdio or HTTP MCP)
       ├── stdio: spawns spanish-grid-mcp as subprocess
       └── HTTP:  connects to spanish-grid-mcp at MCP_SERVER_URL
                  └── docker compose up -d  (separate container)
```

The agentic part is the loop itself — Claude decides which tool to call next based on what it's learned so far. No hand-coded data-gathering pipeline.

All data comes from the [`spanish-grid-mcp`](https://github.com/raulrc/spanish-grid-mcp) server, which wraps ESIOS, REE apidatos, and AEMET OpenData behind an MCP interface.

## Install

```bash
git clone https://github.com/raulrc/spanish-grid-research-agent.git
cd spanish-grid-research-agent
uv sync

cp .env.example .env
# edit .env: add ANTHROPIC_API_KEY, ESIOS_TOKEN, AEMET_TOKEN
```

For the **stdio** path (`agent.py` or `agent_pydantic.py` without `MCP_SERVER_URL`), the [`spanish-grid-mcp`](https://github.com/raulrc/spanish-grid-mcp) package must be importable:

```bash
git clone https://github.com/raulrc/spanish-grid-mcp.git ../spanish-grid-mcp
uv pip install -e ../spanish-grid-mcp
```

For the **HTTP** path, you only need a running MCP server:

```bash
MCP_SERVER_URL=http://localhost:8000/mcp python agent_pydantic.py "..."
```

## Run

```bash
# Hand-rolled loop (stdio only)
python agent.py "Why were Spanish day-ahead prices high on 2026-05-20?"

# Pydantic AI (stdio)
python agent_pydantic.py "Why were Spanish day-ahead prices high on 2026-05-20?"

# Pydantic AI (HTTP — MCP server running elsewhere)
MCP_SERVER_URL=http://spanish-grid-mcp:8000/mcp python agent_pydantic.py "..."
```

Both agents stream tool calls live, then print a final analysis.

## What's good about the code

**`agent.py`** — An explicit agent loop with no framework dependency (~150 lines). The `while True` + tool dispatch is the whole pattern. Uses prompt caching on the system prompt + tool definitions; after the first iteration each subsequent loop step reuses the cached prefix (~10% of full price). Uses adaptive thinking with summarized display — reasoning summaries stream live and each thinking step is cached individually.

**`agent_pydantic.py`** — A Pydantic AI implementation that demonstrates the framework's MCP integration, live event streaming, and usage tracking. Switches between stdio and Streamable HTTP based on the `MCP_SERVER_URL` env var. Uses `MCPToolset` (the recommended API replacing `MCPServerStdio`) and `stream_text(delta=True)` for live output.

## Configuration

| Env var | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | **Required.** |
| `MCP_SERVER_URL` | — | HTTP endpoint for the MCP server (e.g. `http://host:8000/mcp`). If set, connects over Streamable HTTP instead of spawning a subprocess. |
| `MCP_SERVER_CMD` | `python -m spanish_grid_mcp.server` | Command that launches the MCP server subprocess (stdio mode only). |
| `AGENT_MODEL` | `claude-opus-4-7` | Override to a cheaper model like `claude-sonnet-4-6`. |
| `AGENT_MAX_STEPS` | `20` | Hard ceiling on loop iterations (both agents). |
| `ESIOS_TOKEN`, `AEMET_TOKEN` | — | Forwarded to the MCP server subprocess. |

The [`spanish-grid-mcp`](https://github.com/raulrc/spanish-grid-mcp) server also accepts `--transport streamable-http` to run as an HTTP server instead of stdio.

## Status

The MCP server is wired to real data from ESIOS, REE apidatos, and AEMET OpenData. The agent can fetch actual prices, demand, generation mix, CO₂ intensity, and weather observations.

### Known issues

- **`get_cross_border_flows`** returns the demand envelope rather than actual interconnection flows — the REE per-border endpoint returns 500 errors.
