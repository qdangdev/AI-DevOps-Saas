"""Thin Anthropic client wrapper.

Two reasons this exists rather than calling `anthropic.AsyncAnthropic` directly:

  1. **Tool-use convenience.** Every analysis call we make uses tool-use to
     enforce a JSON output schema. Bundling the boilerplate (force tool choice,
     unwrap `tool_use` blocks, raise on stop_reason mismatch) in one helper
     prevents subtle bugs at the call sites.

  2. **Single seam for retries / mocking.** Tests can monkeypatch
     `complete_with_tool` without touching every analyzer module.

This module never knows what the prompts mean — it only knows how to send
messages and unwrap tool_use responses.
"""
from __future__ import annotations

from typing import Any

import structlog
from anthropic import AsyncAnthropic
from anthropic.types import Message

from shared.core.config import get_settings

log = structlog.get_logger(__name__)
settings = get_settings()

_client: AsyncAnthropic | None = None


def get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


class LLMError(Exception):
    """Raised when the LLM returns an unexpected response shape."""


async def complete_with_tool(
    *,
    system: str,
    user: str,
    tool_name: str,
    tool_description: str,
    tool_schema: dict[str, Any],
    model: str | None = None,
    max_tokens: int = 2048,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """Call Claude, force it to invoke `tool_name`, and return the parsed args.

    Tool use is how we get *guaranteed-shape* JSON out of Claude. Two things
    matter:

    - `tool_choice={"type": "tool", "name": ...}` forces Claude to call the
      tool — it cannot reply with prose.
    - `temperature=0` makes the call deterministic for a given prompt.

    The function raises `LLMError` if the response is malformed (it shouldn't
    be, given the forced tool choice, but defensive coding pays here because
    a silent fall-through would let bad data hit the DB).
    """
    client = get_client()
    response: Message = await client.messages.create(
        model=model or settings.llm_model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
        tools=[
            {
                "name": tool_name,
                "description": tool_description,
                "input_schema": tool_schema,
            }
        ],
        tool_choice={"type": "tool", "name": tool_name},
    )

    log.debug(
        "llm.response",
        stop_reason=response.stop_reason,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == tool_name:
            return dict(block.input)

    raise LLMError(f"model did not invoke tool {tool_name!r} (stop_reason={response.stop_reason})")
