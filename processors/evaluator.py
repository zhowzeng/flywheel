"""Evaluator processor: app-log -> feedback (session-based).

從 app-log topic 消費訊息，依 session_id 累積對話紀錄。
當 session 閒置超過 SESSION_TIMEOUT_SECONDS 秒後，呼叫 LLM 分析
整段對話並將 critique 發布到 feedback topic。
"""

from __future__ import annotations

import atexit
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone

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
SESSION_TIMEOUT_SECONDS = 30  # session 閒置超過此秒數即觸發評估

EVALUATOR_SYSTEM_PROMPT = """\
你是一位專業的 prompt 分析師，負責審查客服 AI 與使用者之間的完整對話紀錄。
你的任務是找出 AI 系統 prompt 的缺陷，這些缺陷導致了不理想的回應行為。

請分析對話並指出具體的 prompt 問題，例如：
  - prompt 未說明應使用的語氣或同理心表達方式
  - prompt 缺乏何時使用可用工具的指引
  - prompt 未定義轉接人工客服的條件
  - prompt 沒有提供如何處理邊緣案例的範例
  - prompt 未指定回應語言或格式要求

若你真的無法從對話中找出具體的 prompt 問題，請輸出：無法判斷
只輸出評語本身，不要加任何前綴或說明。"""

# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

openai_client = OpenAI(timeout=30.0)

# confluent_kafka Producer 採用 lazy 初始化，避免啟動時連線失敗阻塞程序
_feedback_producer = None


def _get_feedback_producer():
    global _feedback_producer
    if _feedback_producer is None:
        from confluent_kafka import Producer as KafkaProducer
        _feedback_producer = KafkaProducer({"bootstrap.servers": BROKER_ADDRESS})
        # 程序結束時確保所有訊息都已送出
        atexit.register(_feedback_producer.flush)
    return _feedback_producer


# ---------------------------------------------------------------------------
# Session buffer
# ---------------------------------------------------------------------------

# key: session_id, value: {"messages": [...], "last_seen": float}
_sessions: dict[str, dict] = {}


def _flush_timed_out_sessions() -> list[tuple[str, dict]]:
    """找出所有閒置超過 SESSION_TIMEOUT_SECONDS 的 session，從緩衝區移除並回傳。"""
    now = time.time()
    completed = []
    for sid in list(_sessions):
        idle = now - _sessions[sid]["last_seen"]
        if idle > SESSION_TIMEOUT_SECONDS:
            msg_count = len(_sessions[sid]["messages"])
            print(f"[evaluator] Session {sid} 已閒置 {idle:.1f}s，共 {msg_count} 則訊息，移入評估佇列", flush=True)
            completed.append((sid, _sessions.pop(sid)))
    return completed


def _format_session(messages: list[dict]) -> str:
    """將一個 session 的所有訊息格式化成 LLM 可讀的文字。

    每筆 Kafka 訊息包含完整的 messages 歷史（所有先前輪次）加上本次 response。
    為避免重複列出先前輪次，只取 messages 裡最後一筆 user 訊息，
    以及 response 裡的 assistant 回覆。
    """
    parts = []
    for msg in messages:
        parts.append(f"--- Turn at {msg.get('timestamp', 'unknown')} ---")

        # 從 messages 歷史中只取最後一筆 user 訊息，避免重複輸出先前輪次
        if msg.get("messages"):
            for m in reversed(msg["messages"]):
                if m.get("role") == "user" and m.get("content"):
                    parts.append(f"  [user]: {m['content'][:500]}")
                    break

        # 取出 assistant 的文字回覆與 tool call（若有）
        response = msg.get("response")
        if isinstance(response, dict) and "choices" in response:
            for c in response["choices"]:
                rm = c.get("message", {})
                content = rm.get("content", "")
                if content:
                    parts.append(f"  [assistant]: {content[:500]}")
                tool_calls = rm.get("tool_calls")
                if tool_calls:
                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        parts.append(
                            f"  [tool_call]: {fn.get('name', '?')}"
                            f"({fn.get('arguments', '')})"
                        )

    return "\n".join(parts)


def _evaluate_session(session_id: str, messages: list[dict]) -> None:
    """對一個完整 session 呼叫 LLM 取得 critique，並發布到 feedback topic。"""
    if not messages:
        print(f"[evaluator] Session {session_id} 無訊息，跳過評估", flush=True)
        return

    print(f"[evaluator] 開始評估 session {session_id}，共 {len(messages)} 則訊息", flush=True)
    session_text = _format_session(messages)

    try:
        print(f"[evaluator] 呼叫 LLM 分析 session {session_id}...", flush=True)
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": EVALUATOR_SYSTEM_PROMPT},
                {"role": "user", "content": session_text},
            ],
        )
    except Exception as exc:
        print(f"[evaluator] LLM 呼叫失敗，session {session_id}：{exc}", flush=True)
        return

    critique = (response.choices[0].message.content or "").strip()
    # LLM 回傳「無法判斷」表示對話內容不足以產生有意義的 critique，直接略過
    if not critique or critique.startswith("無法判斷"):
        print(f"[evaluator] Session {session_id} 無可採用的評語，略過", flush=True)
        return

    preview = critique[:80] + ("..." if len(critique) > 80 else "")
    print(f"[evaluator] Session {session_id} 評語：{preview}", flush=True)

    feedback = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "critique": critique,
        "message_count": len(messages),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    producer = _get_feedback_producer()
    producer.produce(
        topic=FEEDBACK_TOPIC,
        key=session_id.encode("utf-8"),
        value=json.dumps(feedback, ensure_ascii=False).encode("utf-8"),
    )
    # poll(0) 讓 librdkafka 處理已送出的訊息回調，避免內部佇列堆積
    producer.poll(0)
    print(f"[evaluator] 已發布 feedback id={feedback['id']} 到 topic={FEEDBACK_TOPIC}", flush=True)


# ---------------------------------------------------------------------------
# Stream processing
# ---------------------------------------------------------------------------


def process_message(msg: dict) -> None:
    """將訊息累積到對應的 session 緩衝區；同時檢查並清除已 timeout 的 session。"""
    session_id = msg.get("session_id")
    if not session_id:
        print("[evaluator] 收到缺少 session_id 的訊息，略過", flush=True)
        return

    now = time.time()

    # 每次收到新訊息時順便清除已 timeout 的其他 session
    for sid, buf in _flush_timed_out_sessions():
        _evaluate_session(sid, buf["messages"])

    # 將訊息加入對應 session 的緩衝區，並更新最後活躍時間
    is_new = session_id not in _sessions
    if is_new:
        _sessions[session_id] = {"messages": [], "last_seen": now}
        print(f"[evaluator] 新 session {session_id}，開始累積訊息", flush=True)
    _sessions[session_id]["messages"].append(msg)
    _sessions[session_id]["last_seen"] = now
    count = len(_sessions[session_id]["messages"])
    print(f"[evaluator] Session {session_id} 累積第 {count} 則訊息", flush=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _start_timeout_flusher() -> None:
    """啟動背景執行緒，每秒掃描一次 session 緩衝區，主動觸發已 timeout 的 session 評估。

    不依賴新訊息進來才觸發，確保即使之後沒有流量也能在 timeout 後立即評估。
    daemon=True 表示主程序結束時此執行緒會一併終止。
    """
    def _loop():
        while True:
            time.sleep(1)
            for sid, buf in _flush_timed_out_sessions():
                _evaluate_session(sid, buf["messages"])

    t = threading.Thread(target=_loop, daemon=True)
    t.start()


if __name__ == "__main__":
    app = Application(
        broker_address=BROKER_ADDRESS,
        consumer_group="evaluator",
        auto_offset_reset="earliest",
    )

    app_log_topic = app.topic(APP_LOG_TOPIC, value_deserializer="json")

    sdf = app.dataframe(topic=app_log_topic)
    sdf = sdf.update(process_message)

    _start_timeout_flusher()
    print(f"[evaluator] Starting (session-based). Broker={BROKER_ADDRESS}", flush=True)
    app.run()
