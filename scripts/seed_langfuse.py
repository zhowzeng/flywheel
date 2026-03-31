"""One-time script: upload the initial prompt to Langfuse.

Run with: uv run scripts/seed_langfuse.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv
from langfuse import Langfuse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.langfuse_prompt import PROMPT_NAME

load_dotenv()

INITIAL_PROMPT = "你是客服機器人，幫助客戶解決問題。你講話很有禮貌，但是廢話很多，喜歡長篇大論。你還喜歡用emoji表達情緒。你還喜歡講冷笑話。"


def main() -> None:
    langfuse = Langfuse()
    result = langfuse.create_prompt(
        name=PROMPT_NAME,
        prompt=INITIAL_PROMPT,
        type="text",
        labels=["production", "latest"],
    )
    print(f"Seeded prompt '{PROMPT_NAME}' to Langfuse (version={result.version}).")
    langfuse.flush()


if __name__ == "__main__":
    main()
