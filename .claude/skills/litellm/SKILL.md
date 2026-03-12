---
name: litellm
description: >
  LiteLLM Python library skill. Use when working with litellm for: unified LLM API calls across
  100+ providers (OpenAI, Anthropic, VertexAI, Azure, Ollama, etc.), completion/acompletion/embedding
  calls, streaming, fallbacks, retries, cost tracking, observability callbacks, or CustomLogger
  integrations. Triggers on any task involving litellm, litellm.completion, litellm.callbacks,
  CustomLogger, or multi-provider LLM routing.
---

# LiteLLM Python SDK

Unified interface for 100+ LLM providers using OpenAI-compatible format.

## Setup

```bash
uv add litellm
```

Set provider API keys as environment variables (e.g. `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`).

## Basic Usage

```python
from litellm import completion, acompletion

# Sync
response = completion(
    model="openai/gpt-4o",
    messages=[{"role": "user", "content": "Hello"}],
)
print(response.choices[0].message.content)

# Async
response = await acompletion(model="anthropic/claude-sonnet-4-6", messages=[...])

# Streaming
for chunk in completion(model="openai/gpt-4o", messages=[...], stream=True):
    print(chunk.choices[0].delta.content or "", end="")
```

## Model Naming

Format: `provider/model-name`

| Provider | Example |
|---|---|
| OpenAI | `openai/gpt-4o` |
| Anthropic | `anthropic/claude-sonnet-4-6` |
| Google VertexAI | `vertex_ai/gemini-2.0-flash` |
| Azure OpenAI | `azure/my-deployment` |
| Ollama | `ollama/llama3` |
| OpenRouter | `openrouter/google/gemini-flash-1.5` |

## Fallbacks & Retries

```python
response = completion(
    model="openai/gpt-4o",
    messages=[...],
    fallbacks=["anthropic/claude-haiku-4-5", "ollama/llama3"],
    num_retries=3,
)
```

## Embeddings

```python
from litellm import embedding

response = embedding(model="openai/text-embedding-3-small", input=["Hello world"])
vectors = response.data[0]["embedding"]
```

## Cost Tracking

```python
from litellm import completion_cost

response = completion(model="openai/gpt-4o", messages=[...])
cost = completion_cost(completion_response=response)
print(f"Cost: ${cost:.6f}")
```

## References

- [Custom Callbacks](references/custom-callbacks.md) — observability, logging, cost hooks via `CustomLogger` or callback functions
