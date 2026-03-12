# LiteLLM Custom Callbacks - Full Reference

## Table of Contents
- [Hook Method Signatures](#hook-method-signatures)
- [kwargs Reference](#kwargs-reference)
- [response_obj Structure](#response_obj-structure)
- [Proxy-Only Hooks](#proxy-only-hooks)
- [Async Patterns](#async-patterns)
- [Full Example](#full-example)

## Hook Method Signatures

```python
class CustomLogger:
    # Called before the API request is sent
    def log_pre_api_call(self, model: str, messages: list, kwargs: dict): ...

    # Called after the API response is received (success or failure)
    def log_post_api_call(self, kwargs: dict, response_obj, start_time, end_time): ...

    # Called only on successful completions
    def log_success_event(self, kwargs: dict, response_obj, start_time, end_time): ...

    # Called only on failures/exceptions
    def log_failure_event(self, kwargs: dict, response_obj, start_time, end_time): ...

    # Async variants (use with litellm.acompletion)
    async def async_log_success_event(self, kwargs: dict, response_obj, start_time, end_time): ...
    async def async_log_failure_event(self, kwargs: dict, response_obj, start_time, end_time): ...
```

`start_time` and `end_time` are `datetime` objects.

## kwargs Reference

```python
# Core request info
kwargs["model"]                                  # str: model name, e.g. "gpt-4o"
kwargs["messages"]                               # list: input messages
kwargs["optional_params"]                        # dict: temperature, max_tokens, etc.

# Cost & caching (available after call completes)
kwargs["response_cost"]                          # float: estimated USD cost
kwargs["cache_hit"]                              # bool: True if served from cache

# LiteLLM internals
kwargs["litellm_params"]["metadata"]             # dict: caller-supplied metadata
kwargs["litellm_params"]["model"]                # str: original model string
kwargs["litellm_params"]["stream"]               # bool

# Provider-specific
kwargs["additional_args"]["complete_input_dict"] # dict: full payload sent to provider
kwargs["additional_args"]["api_base"]            # str: endpoint URL used

# On failure
kwargs["exception"]                              # Exception object
```

## response_obj Structure

For `litellm.completion`, `response_obj` is a `ModelResponse`:

```python
response_obj.id                          # str: unique response ID
response_obj.model                       # str: model used
response_obj.choices[0].message.content  # str: response text
response_obj.usage.prompt_tokens         # int
response_obj.usage.completion_tokens     # int
response_obj.usage.total_tokens          # int
```

On failure hooks, `response_obj` may be `None`.

## Proxy-Only Hooks

These hooks are only available when running litellm as a proxy server:

```python
async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
    # Modify request before sending; return modified data dict
    data["messages"].append({"role": "system", "content": "Be concise."})
    return data

async def async_post_call_success_hook(self, data, user_api_key_dict, response):
    # Modify or inspect response after success
    return response
```

## Async Patterns

Use `async_log_success_event` / `async_log_failure_event` with `litellm.acompletion`:

```python
class AsyncHandler(CustomLogger):
    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        await some_async_db.insert({
            "model": kwargs["model"],
            "cost": kwargs.get("response_cost", 0),
            "tokens": response_obj.usage.total_tokens,
        })

litellm.callbacks = [AsyncHandler()]

# Usage
response = await litellm.acompletion(model="gpt-4o", messages=[...])
```

For sync `litellm.completion`, use `log_success_event` instead. Mixing sync calls with async-only hooks will silently skip those hooks.

## Full Example

```python
from litellm.integrations.custom_logger import CustomLogger
from datetime import datetime
import litellm

class ObservabilityHandler(CustomLogger):
    def log_pre_api_call(self, model, messages, kwargs):
        metadata = kwargs.get("litellm_params", {}).get("metadata", {})
        print(f"[PRE] model={model} user={metadata.get('user_id')}")

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        duration = (end_time - start_time).total_seconds()
        print(
            f"[SUCCESS] model={kwargs['model']} "
            f"cost=${kwargs.get('response_cost', 0):.6f} "
            f"tokens={response_obj.usage.total_tokens} "
            f"duration={duration:.2f}s"
        )

    def log_failure_event(self, kwargs, response_obj, start_time, end_time):
        print(f"[FAILURE] model={kwargs['model']} error={kwargs.get('exception')}")

litellm.callbacks = [ObservabilityHandler()]

response = litellm.completion(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello"}],
    metadata={"user_id": "u_123"},
)
```
