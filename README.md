# Prompt Learning Flywheel

## Prerequisites

- Docker + Docker Compose
- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- OpenAI API key

## Setup

```bash
# 1. Install dependencies
uv sync

# 2. Set environment variables
cp .env.example .env
# Edit .env and fill in OPENAI_API_KEY
```

## Running

Each component runs in a separate terminal.

**Terminal 1 — Redpanda (Kafka broker + Console + topic setup):**
```bash
docker compose up -d
# Redpanda Console: http://localhost:8080
```

**Terminal 2 — Gradio UI:**
```bash
uv run app/gradio_app.py
# UI: http://localhost:7860
```

**Terminal 3 — Evaluator:**
```bash
uv run processors/evaluator.py
```

**Terminal 4 — Learner (Phase 4, not yet implemented):**
```bash
uv run processors/learner.py
```

## Teardown

```bash
docker compose down -v   # -v removes the Redpanda data volume
```

---

## Known Issues / TODOs

### Evaluator / Learner: synchronous OpenAI calls block the Quix Streams consumer loop

`processors/evaluator.py` and `processors/learner.py` call the OpenAI API
synchronously inside the Quix Streams event loop. A hung or slow API call will
block heartbeat delivery to the Kafka broker. The current mitigation is a
`timeout=30.0` on the OpenAI client, which keeps latency well under Kafka's
default `session.timeout.ms`.

A proper fix would be to switch to `AsyncOpenAI` and use Quix Streams' async
processing support, so the consumer loop can continue sending heartbeats while
waiting for the API response. This was deferred because async support in Quix
Streams 3.x is still experimental.
