"""Thin wrapper around the Anthropic client with structured (tool-use) output.

Centralised so extract.py and analyze.py share one client and one retry/parse
path. All calls are synchronous; the async orchestrator runs them via
asyncio.to_thread so the event loop is never blocked.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from config import cfg

_client = None
_init_done = False


def get_client():
    global _client, _init_done
    if _init_done:
        return _client
    _init_done = True
    if not cfg.has_llm:
        _client = None
        return None
    try:
        import anthropic
        # Bounded timeout/retries: the pipeline runs one worker, so a hung call
        # would stall all downstream processing. Fail fast and degrade instead.
        _client = anthropic.Anthropic(
            api_key=cfg.anthropic_api_key, timeout=30.0, max_retries=2
        )
    except Exception:
        _client = None
    return _client


def call_structured(
    system: str,
    user: str,
    tool_name: str,
    schema: dict[str, Any],
    max_tokens: int = 1024,
) -> Optional[dict[str, Any]]:
    """Force a single tool call and return its input dict, or None on failure.

    `schema` is the JSON Schema for the tool's input_schema.
    """
    client = get_client()
    if client is None:
        return None
    try:
        resp = client.messages.create(
            model=cfg.model,
            max_tokens=max_tokens,
            system=system,
            tools=[{"name": tool_name, "description": "Return the structured result.", "input_schema": schema}],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": user}],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
                return block.input  # already a dict
    except Exception as e:  # network / auth / rate limit — degrade gracefully
        print(f"[llm] call failed: {e}")
    return None
