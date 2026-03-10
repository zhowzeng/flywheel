"""Evaluator processor: app-log -> feedback.

Subscribes to the app-log Kafka topic. For each message, calls an LLM to
produce a critique identifying weaknesses in the current prompt. If the LLM
finds a concrete issue, publishes the critique to the feedback topic.
Messages where the LLM cannot identify a specific problem are dropped.
"""

from __future__ import annotations

import json
import os
import uuid

from dotenv import load_dotenv
from openai import OpenAI
from quixstreams import Application

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BROKER_ADDRESS = os.getenv("KAFKA_BROKER", "localhost:19092")
APP_LOG_TOPIC = "app-log"
FEEDBACK_TOPIC = "feedback"

EVALUATOR_SYSTEM_PROMPT = """\
You are a prompt analyst. Your task is to identify weaknesses in a prompt design,
not to comment on whether a specific output is right or wrong.

You will be given: the current prompt, the user input, optionally the expected
output, the actual output, and optionally a user comment.

Analyse what is missing or unclear in the prompt that caused the actual output
to diverge from the expected output (or from what the user expected).
Output specific prompt deficiencies, for example:
  - "The prompt does not restrict the allowed values for category."
  - "The prompt provides no example of the expected sentiment format."
  - "The prompt lacks a concrete output example."

If you genuinely cannot identify a specific prompt problem from the information
given, output exactly: 無法判斷
Output only the critique text. No prefix, no explanation."""

# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

openai_client = OpenAI(timeout=30.0)

# ---------------------------------------------------------------------------
# Stream processing helpers
# ---------------------------------------------------------------------------


_REQUIRED_KEYS = {"id", "prompt_version", "prompt_text", "input"}


def is_evaluable(msg: dict) -> bool:
    """Return True when the message has enough information for evaluation."""
    if not _REQUIRED_KEYS.issubset(msg):
        print(f"[evaluator] Dropping malformed message missing required keys: {_REQUIRED_KEYS - msg.keys()}", flush=True)
        return False
    if not msg.get("actual_output"):
        return False
    if not msg.get("expected_output") and not msg.get("user_comment"):
        return False
    return True


def _build_user_content(msg: dict) -> str:
    parts: list[str] = []

    parts.append(f"## Current Prompt\n{msg['prompt_text']}")
    parts.append(f"## User Input\n{msg['input']}")

    if msg.get("expected_output"):
        parts.append(
            "## Expected Output\n"
            + json.dumps(msg["expected_output"], ensure_ascii=False, indent=2)
        )

    parts.append(
        "## Actual Output\n"
        + json.dumps(msg["actual_output"], ensure_ascii=False, indent=2)
    )

    if msg.get("thumb"):
        parts.append(f"## User Thumb\n{msg['thumb']}")

    if msg.get("user_comment"):
        parts.append(f"## User Comment\n{msg['user_comment']}")

    return "\n\n".join(parts)


def evaluate(msg: dict) -> dict | None:
    """Call the LLM to produce a critique. Returns a feedback dict or None."""
    user_content = _build_user_content(msg)

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": EVALUATOR_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
    except Exception as exc:
        print(f"[evaluator] OpenAI call failed for log_id={msg['id']}: {exc}", flush=True)
        return None

    critique = (response.choices[0].message.content or "").strip()

    if not critique or critique.startswith("無法判斷"):
        print(f"[evaluator] No actionable critique for log_id={msg['id']}, skipping.", flush=True)
        return None

    preview = critique[:80] + ("..." if len(critique) > 80 else "")
    print(f"[evaluator] Critique for log_id={msg['id']} (prompt={msg['prompt_version']}): {preview}", flush=True)

    return {
        "id": str(uuid.uuid4()),
        "log_id": msg["id"],
        "prompt_version": msg["prompt_version"],
        "prompt_text": msg["prompt_text"],
        "thumb": msg.get("thumb"),
        "user_comment": msg.get("user_comment"),
        "critique": critique,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = Application(
        broker_address=BROKER_ADDRESS,
        consumer_group="evaluator",
        auto_offset_reset="earliest",
    )

    app_log_topic = app.topic(APP_LOG_TOPIC, value_deserializer="json")
    feedback_topic = app.topic(FEEDBACK_TOPIC, value_serializer="json")

    sdf = app.dataframe(topic=app_log_topic)
    sdf = sdf.filter(is_evaluable)
    sdf = sdf.apply(evaluate)
    sdf = sdf.filter(lambda x: x is not None)
    sdf.to_topic(feedback_topic)

    print(f"[evaluator] Starting. Broker={BROKER_ADDRESS}", flush=True)
    app.run()
