"""Learner processor: feedback -> Langfuse prompt.

從 feedback topic 消費 critique 訊息，累積到 WINDOW_SIZE 筆後，
呼叫 LLM 根據這批問題產生改良版 system prompt，並儲存到 Langfuse 作為新版本。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from quixstreams import Application

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.langfuse_prompt import create_prompt, get_prompt

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BROKER_ADDRESS = os.getenv("KAFKA_BROKER", "localhost:19092")
FEEDBACK_TOPIC = "feedback"
WINDOW_SIZE = 5  # 累積此數量的 critique 後才觸發一次 prompt 更新

LEARNER_SYSTEM_PROMPT = """\
你是一位專業的 prompt 工程師，負責根據觀察到的對話問題改善客服 AI 的系統 prompt。

規則：
- 只輸出改良後的 prompt 本身，不要加任何說明、前綴或後綴。
- 保留核心目標：一個能透過可用工具（check_order、request_refund、transfer_to_human）
  協助客戶解決問題的客服聊天機器人。
- 針對每個觀察到的問題提出具體改善（例如語氣指引、工具使用條件、升級規則、
  同理心指示、語言風格）。
- 保持 prompt 簡潔且可執行。"""

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

# 暫存尚未達到 WINDOW_SIZE 的 critique 訊息
_critique_buffer: list[dict] = []
openai_client = OpenAI(timeout=30.0)

# ---------------------------------------------------------------------------
# Stream processing helpers
# ---------------------------------------------------------------------------


def is_learnable(msg: dict) -> bool:
    """檢查訊息是否具備學習所需的欄位；不合格的訊息直接過濾掉。"""
    required = {"id", "critique"}
    missing = required - msg.keys()
    if missing:
        print(f"[learner] 訊息缺少必要欄位 {missing}，略過", flush=True)
        return False
    if not msg.get("critique", "").strip():
        print(f"[learner] 訊息 id={msg.get('id')} 的 critique 為空，略過", flush=True)
        return False
    print(f"[learner] 訊息 id={msg.get('id')} 通過驗證，加入緩衝區", flush=True)
    return True


def accumulate(msg: dict) -> list[dict] | None:
    """將 critique 加入緩衝區；累積到 WINDOW_SIZE 筆時回傳該批次，否則回傳 None。"""
    _critique_buffer.append(msg)
    current = len(_critique_buffer)
    if current >= WINDOW_SIZE:
        batch = _critique_buffer[:WINDOW_SIZE]
        _critique_buffer.clear()
        print(f"[learner] 已累積 {WINDOW_SIZE} 筆 critique，觸發 prompt 更新", flush=True)
        return batch
    print(f"[learner] 緩衝區進度 {current}/{WINDOW_SIZE}，等待更多 critique", flush=True)
    return None


def generate_candidate(critiques: list[dict]) -> str | None:
    """根據一批 critique 及目前的 production prompt，讓 LLM 產生改良版 prompt。"""
    # 從 Langfuse 取得目前線上版本的 prompt 作為改寫基礎
    print("[learner] 從 Langfuse 取得目前 production prompt...", flush=True)
    current_prompt = get_prompt(label="production", cache_ttl_seconds=60)
    print(f"[learner] 目前 prompt 長度：{len(current_prompt)} 字元", flush=True)

    numbered = "\n".join(
        f"{i + 1}. {c['critique']}" for i, c in enumerate(critiques)
    )

    user_content = (
        f"## Current Prompt\n\n{current_prompt}\n\n"
        f"## Observed Issues ({len(critiques)} items)\n\n{numbered}\n\n"
        "## Task\n\n"
        "Based on the issues above, output the improved prompt. "
        "Output only the prompt itself."
    )

    try:
        print(f"[learner] 呼叫 LLM 根據 {len(critiques)} 筆 critique 產生新版 prompt...", flush=True)
        response = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": LEARNER_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
        candidate = (response.choices[0].message.content or "").strip() or None
        if candidate:
            print(f"[learner] LLM 產生候選 prompt，長度：{len(candidate)} 字元", flush=True)
        else:
            print("[learner] LLM 回傳空內容", flush=True)
        return candidate
    except Exception as exc:
        print(f"[learner] 呼叫 LLM 失敗：{exc}", flush=True)
        return None


def apply_candidate(critiques: list[dict]) -> None:
    """產生候選 prompt 並存入 Langfuse，同時套用 production 與 latest 標籤。"""
    print(f"[learner] 開始處理批次，共 {len(critiques)} 筆 critique", flush=True)
    candidate = generate_candidate(critiques)
    if candidate is None:
        print(f"[learner] 未能產生候選 prompt，批次 {len(critiques)} 筆 critique 已丟棄", flush=True)
        return None
    try:
        # create_prompt 會在 Langfuse 建立新版本並貼上指定標籤
        new_ver = create_prompt(candidate, labels=["production", "latest"])
        print(f"[learner] 新版 prompt v{new_ver} 已儲存到 Langfuse，標籤：production, latest", flush=True)
    except Exception as exc:
        print(f"[learner] 儲存新版本失敗：{exc}", flush=True)
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
    sdf = sdf.filter(is_learnable)       # 過濾不合格訊息
    sdf = sdf.apply(accumulate)          # 累積到 WINDOW_SIZE 才往下傳
    sdf = sdf.filter(lambda x: x is not None)  # 未達批次大小時阻斷
    sdf = sdf.apply(apply_candidate)     # 產生並儲存新版 prompt

    print(f"[learner] Starting. Broker={BROKER_ADDRESS}", flush=True)
    app.run()
