---
name: langfuse
description: >
  Langfuse LLM observability and evaluation platform skill using the Python SDK.
  Use when working with Langfuse for: tracing LLM applications (spans, generations, events),
  prompt management (versioning, templates, labels), scoring/evaluation, sessions, user tracking,
  or any Langfuse SDK integration. Triggers on tasks involving langfuse, LLM observability,
  trace instrumentation, prompt versioning, or evaluation scoring with Langfuse.
---

# Langfuse Python SDK

## Setup

```bash
uv add langfuse
```

Required environment variables:
```
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_BASE_URL=https://cloud.langfuse.com   # or https://us.cloud.langfuse.com
```

```python
from langfuse import get_client
langfuse = get_client()
```

---

## Tracing

### Context manager (recommended)

```python
with langfuse.start_as_current_observation(as_type="span", name="pipeline", input=user_query) as span:
    with langfuse.start_as_current_observation(as_type="generation", name="llm-call", model="gpt-4o") as gen:
        response = call_llm(...)
        gen.update(output=response, usage_details={"input_tokens": 10, "output_tokens": 50})
    span.update(output=response)

langfuse.flush()  # required in scripts/serverless; not needed in long-running servers
```

### Decorator

```python
from langfuse import observe

@observe()
def my_pipeline(query: str):
    return call_llm(query)

@observe(name="llm-call", as_type="generation")
async def call_llm_async(prompt: str):
    return "response"
```

### Observation types

| `as_type` | Use for |
|-----------|---------|
| `"span"` | Any step or sub-process |
| `"generation"` | LLM calls (tracks model, tokens, cost) |
| `"event"` | Discrete point-in-time events |

### Trace attributes (userId, sessionId, metadata, tags)

Use `propagate_attributes` to set context inherited by all nested observations:

```python
from langfuse import propagate_attributes

with langfuse.start_as_current_observation(as_type="span", name="root"):
    with propagate_attributes(user_id="u123", session_id="sess-abc", metadata={"env": "prod"}, version="1.0"):
        # all nested observations inherit these attributes
        with langfuse.start_as_current_observation(as_type="generation", name="llm"):
            pass
```

---

## Scoring / Evaluation

```python
# Score the current span or trace from within context
with langfuse.start_as_current_observation(as_type="span", name="step") as span:
    span.score(name="correctness", value=0.9, data_type="NUMERIC", comment="Good")
    span.score_trace(name="overall", value=1, data_type="BOOLEAN")

# Score by trace_id (e.g., from async evaluator)
langfuse.create_score(
    name="relevance",
    value=0.8,
    trace_id="<trace_id>",
    observation_id="<obs_id>",  # optional
    data_type="NUMERIC",        # NUMERIC | CATEGORICAL | BOOLEAN
)
```

Get current IDs when needed:
```python
trace_id = langfuse.get_current_trace_id()
obs_id   = langfuse.get_current_observation_id()
```

---

## Prompt Management

See [references/prompt-management.md](references/prompt-management.md) for full details.

Quick reference:

```python
# Fetch production prompt and compile variables
prompt = langfuse.get_prompt("my-prompt")
compiled = prompt.compile(role="expert", question="What is RAG?")

# Create / version a prompt
langfuse.create_prompt(
    name="my-prompt",
    type="text",
    prompt="You are a {{role}}. Answer: {{question}}",
    labels=["production"],
    config={"model": "gpt-4o", "temperature": 0.5},
)

# Link prompt version to a generation trace
with langfuse.start_as_current_observation(as_type="generation", name="llm", model="gpt-4o", prompt=prompt) as gen:
    gen.update(output=call_llm(compiled))
```

---

## Lifecycle

```python
langfuse.flush()     # flush buffered data (use in scripts, tests, serverless)
langfuse.shutdown()  # flush + shutdown background threads
```

Use `langfuse.auth_check()` to verify credentials at startup.
