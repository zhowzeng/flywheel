# Langfuse Prompt Management - Advanced Reference

## Table of Contents
1. [Versions and Labels](#versions-and-labels)
2. [Template Variable Syntax](#template-variable-syntax)
3. [Config Best Practices](#config-best-practices)
4. [OpenAI Integration](#openai-integration)
5. [LangChain Integration](#langchain-integration)
6. [Error Handling](#error-handling)
7. [API Reference](#api-reference)

---

## Versions and Labels

Every `create_prompt` call creates a new version (auto-incremented integer).

Labels are mutable pointers to versions:
- `production` — default label fetched by `get_prompt()`
- `staging` — for pre-production testing
- `latest` — always points to the most recent version (managed by Langfuse)
- Custom labels are supported

Recommended workflow:
1. Create prompt with `labels=[]` (no label — draft)
2. Test with `label="staging"` in staging env
3. Promote to production via Langfuse UI or `labels=["production"]` in create

## Template Variable Syntax

Use `{{variable_name}}` (double curly braces) for template variables.

```python
# Template: "Hello {{name}}, you are {{age}} years old."
compiled = prompt.compile(name="Alice", age=30)
# Result: "Hello Alice, you are 30 years old."
```

For chat prompts, variables work in both `role` and `content` fields.

Missing variables raise an error — always pass all required variables to `compile()`.

## Config Best Practices

Store model parameters in `config` to decouple them from code:

```python
langfuse.create_prompt(
    name="summarizer",
    type="text",
    prompt="Summarize the following: {{text}}",
    config={
        "model": "gpt-4o-mini",
        "temperature": 0.3,
        "max_tokens": 512,
    },
    labels=["production"],
)
```

Retrieve and use at runtime:
```python
prompt = langfuse.get_prompt("summarizer")
cfg = prompt.config

response = openai_client.chat.completions.create(
    model=cfg.get("model", "gpt-4o-mini"),
    temperature=cfg.get("temperature", 0.5),
    max_tokens=cfg.get("max_tokens", 256),
    messages=[{"role": "user", "content": prompt.compile(text=user_text)}],
)
```

## OpenAI Integration

```python
from openai import OpenAI
from langfuse import get_client

langfuse = get_client()
openai_client = OpenAI()

prompt = langfuse.get_prompt("my-chat-prompt", type="chat")
messages = prompt.compile(role="assistant", question=user_input)

response = openai_client.chat.completions.create(
    model=prompt.config.get("model", "gpt-4o-mini"),
    messages=messages,
)
```

## LangChain Integration

```python
from langfuse import get_client
from langchain_core.prompts import ChatPromptTemplate

langfuse = get_client()
prompt = langfuse.get_prompt("my-chat-prompt", type="chat")

# Convert to LangChain format
langchain_prompt = ChatPromptTemplate.from_messages(
    [(m["role"], m["content"]) for m in prompt.prompt]
)
```

## Error Handling

Always provide a fallback for production code:

```python
try:
    prompt = langfuse.get_prompt(
        "my-prompt",
        fallback="You are a helpful assistant. Answer: {{question}}",
    )
except Exception as e:
    # SDK uses fallback automatically; log for visibility
    logger.warning(f"Langfuse prompt fetch failed: {e}")

compiled = prompt.compile(question=user_question)
```

The `fallback` string is used only when the API is unreachable AND the cache is empty.

## API Reference

### `langfuse.create_prompt()`

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | `str` | Prompt identifier |
| `type` | `"text" \| "chat"` | Prompt type |
| `prompt` | `str \| list[dict]` | Template string or messages array |
| `labels` | `list[str]` | Labels to assign (e.g., `["production"]`) |
| `config` | `dict` | Arbitrary metadata (model params, etc.) |

### `langfuse.get_prompt()`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str` | required | Prompt name |
| `type` | `"text" \| "chat"` | `"text"` | Prompt type |
| `version` | `int` | `None` | Specific version number |
| `label` | `str` | `"production"` | Label to fetch |
| `cache_ttl_seconds` | `int` | `60` | Cache TTL; `0` disables cache |
| `fallback` | `str \| list[dict]` | `None` | Fallback if API unavailable |

### `prompt.compile(**variables)`

Returns `str` for text prompts, `list[dict]` for chat prompts.
Raises if any `{{variable}}` in the template is missing from kwargs.
