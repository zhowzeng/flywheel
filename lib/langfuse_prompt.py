"""Langfuse prompt management wrapper.

Provides read/write access to a single named prompt in Langfuse,
replacing the filesystem-based prompt_manager.
"""

from __future__ import annotations

from langfuse import Langfuse

PROMPT_NAME = "customer-service-agent"

_langfuse: Langfuse | None = None


def _client() -> Langfuse:
    global _langfuse
    if _langfuse is None:
        _langfuse = Langfuse()
    return _langfuse


FALLBACK_PROMPT = "You are a helpful customer service assistant."


def get_prompt(label: str = "production", cache_ttl_seconds: int = 60) -> str:
    """Fetch the current prompt text from Langfuse."""
    prompt = _client().get_prompt(
        name=PROMPT_NAME,
        label=label,
        fallback=FALLBACK_PROMPT,
        cache_ttl_seconds=cache_ttl_seconds,
    )
    return prompt.prompt


def create_prompt(text: str, labels: list[str] | None = None) -> int:
    """Create a new prompt version in Langfuse. Returns the version number."""
    result = _client().create_prompt(
        name=PROMPT_NAME,
        prompt=text,
        type="text",
        labels=labels or ["latest"],
    )
    return result.version
