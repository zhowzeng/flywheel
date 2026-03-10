"""Learner processor: feedback -> prompts/.

Subscribes to the feedback Kafka topic. Accumulates critiques in a buffer; once
WINDOW_SIZE critiques have been collected, generates a candidate prompt via LLM
and saves it as a new version directly (no eval gate).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from quixstreams import Application

# Allow importing lib/ from the project root regardless of working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import lib.prompt_manager as prompt_manager

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BROKER_ADDRESS = os.getenv("KAFKA_BROKER", "localhost:19092")
FEEDBACK_TOPIC = "feedback"
WINDOW_SIZE = 5

LEARNER_SYSTEM_PROMPT = """\
你是一位專業的 prompt engineer。你的任務是根據觀察到的問題，改進現有的 prompt。

規則：
- 只輸出改進後的完整 prompt 本身，不要包含任何解釋、前綴或後綴
- 保持 prompt 的核心目標不變（客服對話結構化分析，輸出 JSON）
- 針對每一個觀察到的問題進行具體改進"""

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_critique_buffer: list[dict] = []
openai_client = OpenAI(timeout=30.0)

# ---------------------------------------------------------------------------
# Stream processing helpers
# ---------------------------------------------------------------------------


def is_learnable(msg: dict) -> bool:
    """Return True when the message has all required fields for learning."""
    required = {"id", "prompt_version", "critique"}
    missing = required - msg.keys()
    if missing:
        print(f"[learner] Dropping message missing fields: {missing}", flush=True)
        return False
    if not msg.get("critique", "").strip():
        print(f"[learner] Dropping message with empty critique id={msg.get('id')}", flush=True)
        return False
    return True


def accumulate(msg: dict) -> list[dict] | None:
    """Buffer critiques; return a batch of WINDOW_SIZE when ready."""
    _critique_buffer.append(msg)
    if len(_critique_buffer) >= WINDOW_SIZE:
        batch = _critique_buffer[:WINDOW_SIZE]
        _critique_buffer.clear()
        return batch
    return None


def generate_candidate(critiques: list[dict]) -> str | None:
    """Ask the LLM to produce an improved prompt based on observed critiques."""
    current_prompt = prompt_manager.read_current()

    numbered = "\n".join(
        f"{i + 1}. {c['critique']}" for i, c in enumerate(critiques)
    )

    user_content = (
        f"## 當前 Prompt\n\n{current_prompt}\n\n"
        f"## 觀察到的問題（共 {len(critiques)} 條）\n\n{numbered}\n\n"
        "## 任務\n\n請根據以上問題，輸出改進後的完整 prompt。只輸出 prompt 本身。"
    )

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": LEARNER_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
        return (response.choices[0].message.content or "").strip() or None
    except Exception as exc:
        print(f"[learner] generate_candidate failed: {exc}", flush=True)
        return None


def apply_candidate(critiques: list[dict]) -> None:
    """Generate a candidate prompt and save it as a new version directly."""
    candidate = generate_candidate(critiques)
    if candidate is None:
        return None
    try:
        new_ver = prompt_manager.save_new_version(candidate)
        print(f"[learner] Saved new prompt version v{new_ver}", flush=True)
    except Exception as exc:
        print(f"[learner] Failed to save new version: {exc}", flush=True)
    return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = Application(
        broker_address=BROKER_ADDRESS,
        consumer_group="learner",
        auto_offset_reset="earliest",
    )

    feedback_topic = app.topic(FEEDBACK_TOPIC, value_deserializer="json")

    sdf = app.dataframe(topic=feedback_topic)
    sdf = sdf.filter(is_learnable)
    sdf = sdf.apply(accumulate)
    sdf = sdf.filter(lambda x: x is not None)
    sdf = sdf.apply(apply_candidate)
    sdf = sdf.filter(lambda x: x is not None)  # apply_candidate always returns None

    print(f"[learner] Starting. Broker={BROKER_ADDRESS}", flush=True)
    app.run()
