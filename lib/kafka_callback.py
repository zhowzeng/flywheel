"""LiteLLM custom callback that publishes completion data to Kafka.

This module is a pluggable component: register it via
``litellm.callbacks = [KafkaCallback()]`` and unregister by removing it.

Only messages whose metadata contains a ``session_id`` are published;
internal LLM calls (evaluator, learner) are silently skipped.
"""

from __future__ import annotations

import atexit
import json
import os
from datetime import datetime, timezone

from confluent_kafka import Producer as KafkaProducer
from litellm.integrations.custom_logger import CustomLogger

BROKER_ADDRESS = os.getenv("KAFKA_BROKER", "localhost:19092")
APP_LOG_TOPIC = "app-log"


class KafkaCallback(CustomLogger):

    def __init__(self) -> None:
        super().__init__()
        self._producer = KafkaProducer({"bootstrap.servers": BROKER_ADDRESS})
        atexit.register(self._producer.flush)

    # -- async success hook ---------------------------------------------------

    async def async_log_success_event(
        self, kwargs, response_obj, start_time, end_time
    ):
        metadata = kwargs.get("litellm_params", {}).get("metadata", {})
        session_id = metadata.get("session_id")
        if not session_id:
            return

        message = {
            "session_id": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": kwargs.get("model", ""),
            "messages": _serialisable_messages(kwargs.get("messages")),
            "response": _serialisable_response(response_obj),
            "start_time": start_time.isoformat() if start_time else None,
            "end_time": end_time.isoformat() if end_time else None,
        }
        self._producer.produce(
            topic=APP_LOG_TOPIC,
            key=session_id.encode("utf-8"),
            value=json.dumps(message, ensure_ascii=False, default=str).encode("utf-8"),
        )
        self._producer.poll(0)


# -- helpers ------------------------------------------------------------------


def _serialisable_messages(messages) -> list[dict] | None:
    if messages is None:
        return None
    result = []
    for m in messages:
        if isinstance(m, dict):
            result.append(m)
        elif hasattr(m, "model_dump"):
            result.append(m.model_dump())
        else:
            result.append({"content": str(m)})
    return result


def _serialisable_response(response_obj) -> dict | str | None:
    if response_obj is None:
        return None
    if hasattr(response_obj, "model_dump"):
        return response_obj.model_dump()
    return str(response_obj)
