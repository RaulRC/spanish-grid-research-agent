"""Streamlit chat UI for the Spanish Grid Research Agent.

Usage:
    streamlit run streamlit_app.py
"""
from __future__ import annotations

import asyncio
import os
import queue
import threading
from collections.abc import Generator

import streamlit as st

from agent_pydantic import (
    AgentEvent,
    LogEvent,
    TextDeltaEvent,
    ToolCallEvent,
    ToolResultEvent,
    UsageEvent,
    run_agent,
)


def _iter_agent(question: str) -> Generator[AgentEvent, None, None]:
    """Run the async generator in a dedicated thread with its own event loop,
    preserving task/cancel-scope affinity throughout the entire agent run."""
    q: queue.Queue[AgentEvent | None] = queue.Queue()

    async def _run() -> None:
        async for event in run_agent(question):
            q.put(event)
        q.put(None)

    threading.Thread(target=lambda: asyncio.run(_run()), daemon=True).start()

    while True:
        event = q.get()
        if event is None:
            break
        yield event


# --- Page config ------------------------------------------------------------

st.set_page_config(
    page_title="Spanish Grid Research Agent",
    page_icon="⚡",
    layout="wide",
)

st.title("⚡ Spanish Grid Research Agent")
st.caption(
    "Ask anything about the Spanish electricity market — prices, demand, generation, CO₂, weather."
)


# --- Sidebar ----------------------------------------------------------------

with st.sidebar:
    st.header("Configuration")
    model = st.text_input("Model", value=os.getenv("AGENT_MODEL", "claude-sonnet-4-6"))
    max_steps = st.number_input("Max steps", min_value=1, max_value=50, value=20)

    if st.button("🗑️ New conversation", use_container_width=True):
        st.session_state.messages = []
        st.session_state.last_usage = None
        st.rerun()

    if st.session_state.get("last_usage"):
        u = st.session_state.last_usage
        st.divider()
        st.caption("Last run usage")
        st.code(
            f"Input:  {u['input_tokens']:,}\n"
            f"Output: {u['output_tokens']:,}\n"
            f"Cache read:  {u['cache_read']:,}\n"
            f"Cache write: {u['cache_write']:,}"
        )


# --- Chat messages ----------------------------------------------------------

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if "tool_calls" in msg:
            with st.expander("🔧 Tool calls", expanded=False):
                for tc in msg["tool_calls"]:
                    st.code(tc)


# --- Chat input -------------------------------------------------------------

if prompt := st.chat_input("Ask about Spanish electricity..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        status = st.status("Initializing...")
        tool_box = st.empty()
        text_placeholder = st.empty()
        full_text = ""
        tool_calls: list[str] = []

        for event in _iter_agent(prompt):
            if isinstance(event, LogEvent):
                status.update(label=event.message)
            elif isinstance(event, ToolCallEvent):
                label = f"🔧 {event.name}(...)"
                status.update(label=label)
                tool_calls.append(f"{event.name}({event.args})")
            elif isinstance(event, ToolResultEvent):
                tool_box.code(event.content_preview)
            elif isinstance(event, TextDeltaEvent):
                full_text += event.content
                text_placeholder.markdown(full_text + "▌")
            elif isinstance(event, UsageEvent):
                status.update(
                    label=f"Done — {event.input_tokens:,} in / {event.output_tokens:,} out"
                )
                st.session_state.last_usage = {
                    "input_tokens": event.input_tokens,
                    "output_tokens": event.output_tokens,
                    "cache_read": event.cache_read,
                    "cache_write": event.cache_write,
                }

        text_placeholder.markdown(full_text)
        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": full_text,
                "tool_calls": tool_calls,
            }
        )
