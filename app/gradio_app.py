"""Gradio UI for the Prompt Learning Flywheel demo.

Responsibilities:
- Display current prompt version and content (read-only)
- Accept customer service dialogue -> call OpenAI -> display JSON output
- Display expected output from golden set for comparison
- Collect human feedback (thumb + comment) and publish to app-log Kafka topic
- Show prompt version history and diff viewer
- Batch run golden set test cases
"""

from __future__ import annotations

import atexit
import difflib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import gradio as gr
import pandas as pd
from confluent_kafka import Producer as KafkaProducer
from dotenv import load_dotenv
from openai import OpenAI

# Make lib importable when running from any directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.prompt_manager import (
    current_mtime,
    current_version,
    list_versions,
    read_current,
    read_version,
    set_current_to_version,
)

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BROKER_ADDRESS = os.getenv("KAFKA_BROKER", "localhost:19092")
APP_LOG_TOPIC = "app-log"
GOLDEN_SET_PATH = Path(__file__).resolve().parent.parent / "golden_set.json"

try:
    golden_set: list[dict] = json.loads(GOLDEN_SET_PATH.read_text(encoding="utf-8"))
except FileNotFoundError:
    sys.exit(f"ERROR: golden_set.json not found at {GOLDEN_SET_PATH}")

# ---------------------------------------------------------------------------
# Clients (module-level singletons)
# ---------------------------------------------------------------------------

openai_client = OpenAI()

_kafka_producer = KafkaProducer({"bootstrap.servers": BROKER_ADDRESS})
atexit.register(_kafka_producer.flush)


# ---------------------------------------------------------------------------
# Kafka helper
# ---------------------------------------------------------------------------


def publish_app_log(
    *,
    log_id: str,
    prompt_version: int,
    prompt_text: str,
    input_text: str,
    expected_output: dict | None,
    actual_output: dict | None,
    thumb: str | None = None,
    user_comment: str | None = None,
) -> None:
    message = {
        "id": log_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prompt_version": f"v{prompt_version}",
        "prompt_text": prompt_text,
        "input": input_text,
        "expected_output": expected_output,
        "actual_output": actual_output,
        "thumb": thumb,
        "user_comment": user_comment,
    }
    _kafka_producer.produce(
        topic=APP_LOG_TOPIC,
        key=log_id.encode("utf-8"),
        value=json.dumps(message, ensure_ascii=False).encode("utf-8"),
    )
    _kafka_producer.flush()


# ---------------------------------------------------------------------------
# LLM inference
# ---------------------------------------------------------------------------


def call_llm(prompt_text: str, input_text: str) -> dict:
    """Call OpenAI and return parsed JSON dict.

    Raises ValueError with a user-readable message on failure.
    """
    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": prompt_text},
            {"role": "user", "content": input_text},
        ],
        response_format={"type": "json_object"},
    )
    if not response.choices:
        raise ValueError("OpenAI returned no choices")
    content = response.choices[0].message.content
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"OpenAI returned non-JSON content: {content!r}") from exc


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------


def _prompt_version_label() -> str:
    return f"v{current_version()}"


def _version_history_rows() -> list[list[str]]:
    rows = []
    for v in list_versions():
        mtime_str = datetime.fromtimestamp(v["mtime"], tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
        rows.append([f"v{v['version']}", mtime_str])
    return rows


def _version_choices() -> list[int]:
    return [v["version"] for v in list_versions()]


def _diff_text(ver_a: int, ver_b: int) -> str:
    try:
        text_a = read_version(ver_a)
        text_b = read_version(ver_b)
        lines = difflib.unified_diff(
            text_a.splitlines(keepends=True),
            text_b.splitlines(keepends=True),
            fromfile=f"v{ver_a}.prompt.md",
            tofile=f"v{ver_b}.prompt.md",
        )
        result = "".join(lines)
        return result if result else "(no differences)"
    except FileNotFoundError as exc:
        return f"Error: {exc}"


# ---------------------------------------------------------------------------
# Gradio event handlers
# ---------------------------------------------------------------------------


def on_run_inference(input_text: str, state: dict) -> tuple:
    """Run LLM inference. Returns (output_json_str, new_state)."""
    if not input_text.strip():
        return "Please enter a customer service dialogue", state

    version = current_version()
    prompt_text = read_current()
    try:
        actual = call_llm(prompt_text, input_text)
    except Exception as exc:
        return f"ERROR: {exc}", state

    log_id = str(uuid.uuid4())
    new_state = {
        "log_id": log_id,
        "prompt_version": version,
        "prompt_text": prompt_text,
        "input": input_text,
        "expected": state.get("expected"),
        "actual": actual,
    }
    return json.dumps(actual, ensure_ascii=False, indent=2), new_state


def on_load_golden_case(case_id: str, state: dict) -> tuple:
    """Load a golden set case. Returns (input_text, expected_json_str, new_state).

    Updates inference_state with expected output atomically to avoid the race
    condition that would arise from wiring two separate .change() handlers.
    """
    if not case_id:
        return "", "", {**state, "expected": None}
    case = next((c for c in golden_set if c["id"] == case_id), None)
    if not case:
        return "", "", {**state, "expected": None}
    return (
        case["input"],
        json.dumps(case["expected_output"], ensure_ascii=False, indent=2),
        {**state, "expected": case["expected_output"]},
    )


def on_submit_feedback(thumb: str, user_comment: str, state: dict) -> dict:
    """Publish app-log message with feedback. Returns new_state.

    Clears log_id after first submission to prevent duplicate feedback.
    """
    if not state.get("log_id"):
        gr.Warning("Please run inference first.")
        return state
    if not thumb:
        gr.Warning("Please select a thumb before submitting.")
        return state
    publish_app_log(
        log_id=state["log_id"],
        prompt_version=state["prompt_version"],
        prompt_text=state["prompt_text"],
        input_text=state["input"],
        expected_output=state.get("expected"),
        actual_output=state["actual"],
        thumb=thumb,
        user_comment=user_comment.strip() or None,
    )
    gr.Info(f"Feedback submitted ({thumb})")
    return {**state, "log_id": None}


def on_check_prompt_update(stored_mtime: float) -> tuple:
    """Called by gr.Timer every 2s. Returns updated values only if mtime changed.

    Returns 6 fixed values + one value per golden-set case (for eval tables).
    Note: does NOT run eval computation to keep the timer response fast.
    """
    _no_change = (gr.update(), stored_mtime, gr.update(), gr.update(), gr.update(), gr.update()) + tuple(
        gr.update() for _ in golden_set
    )
    try:
        current = current_mtime()
    except OSError:
        return _no_change
    if current != stored_mtime:
        try:
            choices = _version_choices()
            return (
                read_current(),
                current,
                _version_history_rows(),
                gr.update(choices=choices),
                gr.update(choices=choices),
                gr.update(choices=choices),
                *_build_case_dataframes(),
            )
        except OSError:
            return _no_change
    return _no_change


def on_show_diff(ver_a: int, ver_b: int) -> str:
    return _diff_text(int(ver_a), int(ver_b))


# module-level cache: version_number -> {case_id -> result dict}
# lives for the process lifetime; cleared on restart
_eval_cache: dict[int, dict[str, dict]] = {}

_EVAL_HEADERS = ["Version", "Category", "Sentiment", "Summary", "Action", "Score"]


def _build_case_dataframes() -> list[pd.DataFrame]:
    """Return one DataFrame per golden-set case.

    Each DataFrame has rows = cached versions (sorted) and columns defined by
    _EVAL_HEADERS. Returns empty DataFrames when the cache is empty.
    """
    sorted_versions = sorted(_eval_cache.keys())
    dfs = []
    for case in golden_set:
        rows = []
        for ver_num in sorted_versions:
            result = _eval_cache[ver_num].get(case["id"], {})
            rows.append({
                "Version": f"v{ver_num}",
                "Category": result.get("category", ""),
                "Sentiment": result.get("sentiment", ""),
                "Summary": result.get("summary", ""),
                "Action": result.get("suggested_action", ""),
                "Score": result.get("score", ""),
            })
        dfs.append(pd.DataFrame(rows, columns=_EVAL_HEADERS) if rows else pd.DataFrame(columns=_EVAL_HEADERS))
    return dfs


def _run_version_eval_compute() -> None:
    """Run golden set against every prompt version, populating _eval_cache.

    Cached versions are skipped on subsequent runs.
    """
    versions = list_versions()
    for v in versions:
        ver_num = v["version"]
        if ver_num in _eval_cache:
            continue
        try:
            prompt_text = read_version(ver_num)
        except Exception:
            _eval_cache[ver_num] = {
                case["id"]: {
                    "category": "ERROR", "sentiment": "ERROR",
                    "summary": "ERROR", "suggested_action": "ERROR",
                    "score": "0/2",
                }
                for case in golden_set
            }
            continue

        ver_results: dict[str, dict] = {}
        for case in golden_set:
            exp = case["expected_output"]
            try:
                response = openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": prompt_text},
                        {"role": "user", "content": case["input"]},
                    ],
                    response_format={"type": "json_object"},
                )
                actual = json.loads(response.choices[0].message.content or "{}")
                actual_cat = actual.get("category", "")
                actual_sent = actual.get("sentiment", "")
                cat_ok = actual_cat == exp.get("category", "")
                sent_ok = actual_sent == exp.get("sentiment", "")
                ver_results[case["id"]] = {
                    "category": actual_cat,
                    "sentiment": actual_sent,
                    "summary": actual.get("summary", ""),
                    "suggested_action": actual.get("suggested_action", ""),
                    "score": f"{(1 if cat_ok else 0) + (1 if sent_ok else 0)}/2",
                }
            except Exception:
                ver_results[case["id"]] = {
                    "category": "ERROR", "sentiment": "ERROR",
                    "summary": "ERROR", "suggested_action": "ERROR",
                    "score": "0/2",
                }
        _eval_cache[ver_num] = ver_results


def on_run_version_eval() -> list[pd.DataFrame]:
    """Run eval for uncached versions. Returns one DataFrame per golden-set case."""
    _run_version_eval_compute()
    return _build_case_dataframes()


def on_rerun_eval() -> list[pd.DataFrame]:
    """Clear eval cache and re-evaluate all versions."""
    _eval_cache.clear()
    _run_version_eval_compute()
    return _build_case_dataframes()


def on_set_current_version(version_num: int) -> None:
    try:
        set_current_to_version(int(version_num))
        gr.Info(f"v{int(version_num)} is now current.")
    except Exception as exc:
        gr.Warning(f"Error: {exc}")


# ---------------------------------------------------------------------------
# UI build
# ---------------------------------------------------------------------------


def build_ui() -> gr.Blocks:
    golden_choices = [
        (f"{c['id']}: {c['description']}", c["id"]) for c in golden_set
    ]

    with gr.Blocks(title="Prompt Learning Flywheel") as demo:
        gr.Markdown("# Prompt Learning Flywheel")

        mtime_state = gr.State(value=current_mtime())
        inference_state = gr.State(value={})

        with gr.Tabs():

            # ------------------------------------------------------------------
            # Tab 1: Inference
            # ------------------------------------------------------------------
            with gr.Tab("Inference"):

                prompt_text_box = gr.Textbox(
                    label="Current Prompt",
                    value=read_current(),
                    lines=5,
                    interactive=False,
                )

                gr.Markdown("---")

                with gr.Row():
                    with gr.Column():
                        golden_dropdown = gr.Dropdown(
                            label="Load Golden Set Case (optional)",
                            choices=golden_choices,
                            value=None,
                        )
                        input_box = gr.Textbox(
                            label="Customer Service Dialogue",
                            lines=4,
                            placeholder="Enter customer service dialogue...",
                        )
                        expected_box = gr.Textbox(
                            label="Expected Output",
                            lines=6,
                            interactive=False,
                        )
                        run_btn = gr.Button("Run Inference", variant="primary")

                    with gr.Column():
                        output_box = gr.Textbox(
                            label="Actual Output (JSON)",
                            lines=8,
                            interactive=False,
                        )
                        gr.Markdown("### Feedback")
                        thumb_radio = gr.Radio(
                            choices=["up", "down"],
                            label="Thumb",
                            value=None,
                        )
                        comment_box = gr.Textbox(
                            label="Comment (optional)",
                            placeholder="Describe what went wrong...",
                            lines=2,
                        )
                        submit_feedback_btn = gr.Button(
                            "Submit Feedback", variant="primary"
                        )

            # ------------------------------------------------------------------
            # Tab 2: Prompt Management
            # ------------------------------------------------------------------
            with gr.Tab("Prompt Management"):
                with gr.Row():
                    with gr.Column():
                        history_table = gr.Dataframe(
                            headers=["Version", "Modified At"],
                            label="Version History",
                            value=_version_history_rows(),
                        )
                        refresh_btn = gr.Button("Refresh")
                        refresh_btn.click(
                            fn=_version_history_rows,
                            inputs=[],
                            outputs=[history_table],
                        )
                        gr.Markdown("### Set as Current")
                        set_version_input = gr.Dropdown(
                            label="Version",
                            choices=_version_choices(),
                            value=None,
                        )
                        set_current_btn = gr.Button("Set as Current", variant="primary")
                        set_current_btn.click(
                            fn=on_set_current_version,
                            inputs=[set_version_input],
                            outputs=[],
                        )

                    with gr.Column():
                        gr.Markdown("### Diff Viewer")
                        with gr.Row():
                            diff_from = gr.Dropdown(
                                label="From version",
                                choices=_version_choices(),
                                value=None,
                            )
                            diff_to = gr.Dropdown(
                                label="To version",
                                choices=_version_choices(),
                                value=None,
                            )
                        diff_btn = gr.Button("Show Diff")
                        diff_output = gr.Code(label="Diff", language=None, lines=15)
                        diff_btn.click(
                            fn=on_show_diff,
                            inputs=[diff_from, diff_to],
                            outputs=[diff_output],
                        )


            # ------------------------------------------------------------------
            # Tab 3: Golden Eval
            # ------------------------------------------------------------------
            with gr.Tab("Golden Eval"):
                with gr.Row():
                    run_eval_btn = gr.Button("Run (new versions only)", variant="primary")
                    rerun_eval_btn = gr.Button("Rerun All Versions", variant="secondary")
                case_tables: list[gr.Dataframe] = []
                for case in golden_set:
                    gr.Markdown(f"**{case['id']}**: {case['description']}")
                    tbl = gr.Dataframe(
                        headers=_EVAL_HEADERS,
                        label=None,
                        interactive=False,
                    )
                    case_tables.append(tbl)
                run_eval_btn.click(
                    fn=on_run_version_eval,
                    inputs=[],
                    outputs=case_tables,
                )
                rerun_eval_btn.click(
                    fn=on_rerun_eval,
                    inputs=[],
                    outputs=case_tables,
                )

        # ----------------------------------------------------------------------
        # Event wiring
        # ----------------------------------------------------------------------

        # Single handler for golden dropdown: updates UI + state atomically
        golden_dropdown.change(
            fn=on_load_golden_case,
            inputs=[golden_dropdown, inference_state],
            outputs=[input_box, expected_box, inference_state],
        )

        run_btn.click(
            fn=on_run_inference,
            inputs=[input_box, inference_state],
            outputs=[output_box, inference_state],
        )

        submit_feedback_btn.click(
            fn=on_submit_feedback,
            inputs=[thumb_radio, comment_box, inference_state],
            outputs=[inference_state],
        )

        # Polling: detect prompt file changes every 2 seconds
        timer = gr.Timer(value=2)
        timer.tick(
            fn=on_check_prompt_update,
            inputs=[mtime_state],
            outputs=[prompt_text_box, mtime_state, history_table, set_version_input, diff_from, diff_to, *case_tables],
        )

    return demo


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    demo = build_ui()
    demo.launch()
