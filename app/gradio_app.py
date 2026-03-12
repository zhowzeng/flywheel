"""Gradio multi-turn chatbot for the Prompt Learning Flywheel v2.

Uses openai-agents SDK with LiteLLM model provider. LLM calls are
automatically logged to Kafka via the KafkaCallback.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import gradio as gr
from gradio import ChatMessage
import litellm
from agents import Agent, ModelSettings, RunConfig, Runner
from agents.extensions.models.litellm_model import LitellmModel
from agents import ItemHelpers
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.tools import (
    dispute_transaction,
    freeze_card,
    get_account_balance,
    get_transaction_history,
    report_fraud,
    escalate_to_human,
)
from lib.kafka_callback import KafkaCallback
from lib.langfuse_prompt import get_prompt

load_dotenv(override=True)

# ---------------------------------------------------------------------------
# LiteLLM callback registration
# ---------------------------------------------------------------------------

kafka_cb = KafkaCallback()
litellm.callbacks = [kafka_cb]

# ---------------------------------------------------------------------------
# Agent helpers
# ---------------------------------------------------------------------------

MODEL_ID = "openai/gpt-4.1-mini"


def _build_agent() -> Agent:
    instructions = get_prompt(label="production", cache_ttl_seconds=5)
    return Agent(
        name="CustomerServiceAgent",
        model=LitellmModel(model=MODEL_ID),
        instructions=instructions,
        tools=[
            escalate_to_human,
            get_account_balance,
            get_transaction_history,
            freeze_card,
            dispute_transaction,
            report_fraud,
        ],
    )


# ---------------------------------------------------------------------------
# Gradio UI
# ---------------------------------------------------------------------------

def build_ui() -> gr.Blocks:
    with gr.Blocks(title="Customer Service Agent") as demo:
        gr.Markdown("# 銀行客服 Agent")
        session_id_state = gr.State(value=lambda: str(uuid.uuid4()))

        with gr.Accordion("Current Prompt", open=False):
            prompt_display = gr.Textbox(
                value=get_prompt(label="production", cache_ttl_seconds=5),
                lines=10,
                interactive=False,
                show_label=False,
            )
            refresh_btn = gr.Button("Refresh Prompt", variant="secondary", size="sm")

        def refresh_prompt():
            return get_prompt(label="production", cache_ttl_seconds=5)

        refresh_btn.click(fn=refresh_prompt, inputs=[], outputs=[prompt_display])

        async def chat_fn(
            message: str,
            history: list[dict],
            session_id: str,
        ):
            agent = _build_agent()

            def _extract_text(content: str | list) -> str:
                if isinstance(content, str):
                    return content
                return " ".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in content
                )

            input_messages = [
                {"role": m["role"], "content": _extract_text(m["content"])}
                for m in history
                if m["role"] in ("user", "assistant")
            ]
            input_messages.append({"role": "user", "content": message})

            config = RunConfig(
                model_settings=ModelSettings(metadata={"session_id": session_id}),
            )

            yielded = False
            result = Runner.run_streamed(agent, input_messages, run_config=config)
            async for event in result.stream_events():
                if event.type != "run_item_stream_event":
                    continue
                if event.item.type == "tool_call_item":
                    tool_name = event.item.raw_item.name
                    yield ChatMessage(
                        role="assistant",
                        content="",
                        metadata={"title": f"Tool call: {tool_name}"},
                    )
                    yielded = True
                elif event.item.type == "tool_call_output_item":
                    yield ChatMessage(
                        role="assistant",
                        content=str(event.item.output),
                        metadata={"title": "Tool output"},
                    )
                    yielded = True
                elif event.item.type == "message_output_item":
                    text = ItemHelpers.text_message_output(event.item)
                    yield ChatMessage(role="assistant", content=text)
                    yielded = True
            if not yielded:
                yield result.final_output or ""

        # history_state stores plain dicts for agent input; chatbot displays ChatMessage
        history_state = gr.State(value=[])
        chatbot = gr.Chatbot()

        with gr.Row():
            msg_input = gr.Textbox(
                placeholder="Type a message...",
                show_label=False,
                scale=9,
                submit_btn=True,
            )
            new_conv_btn = gr.Button("New Conversation", variant="secondary", scale=1)

        async def respond(message: str, history: list[dict], session_id: str):
            new_history = history + [{"role": "user", "content": message}]
            display = list(new_history)
            last_chunk = None

            async for chunk in chat_fn(message, history, session_id):
                last_chunk = chunk
                yield "", display + [chunk], new_history

            # persist final assistant text to plain-dict history for next turn
            if isinstance(last_chunk, ChatMessage) and not getattr(last_chunk, "metadata", None):
                new_history = new_history + [{"role": "assistant", "content": last_chunk.content}]

            if last_chunk is not None:
                yield "", display + [last_chunk], new_history

        msg_input.submit(
            fn=respond,
            inputs=[msg_input, history_state, session_id_state],
            outputs=[msg_input, chatbot, history_state],
        )

        def new_conversation():
            return [], [], str(uuid.uuid4())

        new_conv_btn.click(
            fn=new_conversation,
            inputs=[],
            outputs=[chatbot, history_state, session_id_state],
        )

    return demo


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    demo = build_ui()
    demo.launch()
