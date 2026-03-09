# Prompt Learning Flywheel — POC Plan

## 目標

驗證「LLM application 可以透過自動化流程持續改進自身 prompt」這個概念。

Demo 場景：客服對話 → 結構化 JSON 擷取。初始 prompt 粗糙，flywheel 跑數輪後，prompt 自動演化，輸出品質可觀察地提升。

---

## 架構總覽

```
┌────────────┐      ┌──────────────┐      ┌──────────────────────┐
│  Gradio UI │─────▶│  Redpanda    │─────▶│  Quix Streams         │
│            │◀─ ─ ─│              │◀─────│                       │
└────────────┘      └──────────────┘      └──────────────────────┘
      │                    │                    │
      │ 讀 prompt          │ Topics:            │ 兩個 processor:
      ▼                    │  • app-log          │  1. Evaluator
┌────────────┐             │  • feedback         │  2. Learner
│Prompt Store│             │  • prompt-store     │
│(compacted  │             │                     │
│ topic)     │◀────────────┼─────────────────────┘
└────────────┘
```

### 資料流

```
1. User 在 Gradio 輸入客服對話 (或跑預設 test case)
2. Gradio App 從 prompt-store topic 讀取當前 prompt
3. 呼叫 OpenAI API 產生結構化 JSON
4. 將 {input, output, expected_output, prompt_version} pub 到 app-log topic
5. Quix Evaluator sub app-log，比對 output vs expected_output，產生評分
6. 評分 pub 到 feedback topic
7. Quix Learner sub feedback，累積 5 條後觸發
8. Learner 用 OpenAI 根據失敗案例產生 candidate prompt
9. Candidate prompt 跑 golden set eval gate
10. 通過 → pub 到 prompt-store topic (new version)
    不通過 → 丟棄，等下一批
11. Gradio App 偵測到新版 prompt，UI 自動更新
```

---

## 技術棧

| Component | 技術 | 說明 |
|-----------|------|------|
| LLM | OpenAI API (gpt-4o-mini) | 便宜快速，POC 夠用 |
| LLM App UI | Gradio | 展示 + 人工測試 |
| 流處理平台 | Redpanda (Docker) | 單節點，3 個 topic |
| 流處理框架 | Quix Streams (Python) | 兩個 processor |
| Prompt Store | Redpanda compacted topic | 不需額外 infra |

---

## Components 細節

### 1. Redpanda (Docker)

**Topics:**

| Topic | Key | Compaction | 用途 |
|-------|-----|------------|------|
| `app-log` | UUID | 不壓縮 | LLM app 的每次呼叫紀錄 |
| `feedback` | UUID | 不壓縮 | evaluator 產生的評分 |
| `prompt-store` | `"current"` | log compaction | 只保留最新 prompt |

**app-log message schema:**
```json
{
  "id": "uuid",
  "timestamp": "ISO8601",
  "prompt_version": 1,
  "input": "客服對話原文...",
  "expected_output": { ... },
  "actual_output": { ... }
}
```

**feedback message schema:**
```json
{
  "id": "uuid",
  "log_id": "ref to app-log id",
  "prompt_version": 1,
  "score": 0.6,
  "details": {
    "format_correct": true,
    "category_match": false,
    "sentiment_match": true,
    "summary_quality": 0.4
  },
  "source": "auto"
}
```

**prompt-store message schema:**
```json
{
  "version": 2,
  "prompt": "你是一個客服對話分析助手...",
  "created_at": "ISO8601",
  "trigger_feedback_ids": ["uuid1", "uuid2", ...],
  "eval_gate_score": 0.85,
  "parent_version": 1
}
```

### 2. Gradio App

**功能：**
- 顯示當前 prompt 版本與內容 (唯讀)
- 輸入客服對話 → 呼叫 OpenAI → 顯示 JSON 輸出
- 顯示 expected output 供比對
- 提供 👍👎 人工 feedback (pub 到 feedback topic)
- 版本歷史列表 (version, score, timestamp)
- Prompt diff 檢視 (v(n-1) → v(n))
- 一鍵「Run All Test Cases」按鈕，批次跑 golden set

**Prompt 讀取機制：**
- 背景 thread 持續 consume prompt-store topic
- 本地快取最新版 prompt + 歷史版本
- gr.Timer 每 2 秒檢查是否有新版，有就更新 UI

### 3. Quix Evaluator

**輸入：** sub `app-log` topic
**輸出：** pub 到 `feedback` topic

**評分邏輯 (programmatic, 不用 LLM)：**
```
score = weighted average of:
  - format_correct (0 or 1):    JSON 格式是否正確，欄位是否齊全    weight: 0.3
  - category_match (0 or 1):    問題類型是否正確                    weight: 0.3
  - sentiment_match (0 or 1):   情緒判斷是否正確                    weight: 0.2
  - summary_quality (0-1):      摘要與 expected 的語意相似度         weight: 0.2
                                (可用簡單的 embedding cosine sim)
```

重點：evaluator 盡量用規則和 embedding，不用 LLM judge，避免 LLM 評 LLM 的問題。

### 4. Quix Learner

**輸入：** sub `feedback` topic
**觸發條件：** 累積 5 條 feedback

**流程：**
1. 收集 5 條 feedback + 對應的 app-log (input/output/expected)
2. 讀取當前 prompt (從本地快取)
3. 組裝 meta-prompt 給 OpenAI：

```
你是一個 prompt engineer。以下是當前 prompt 和它最近 5 次的表現。
請分析失敗模式，產生改進後的 prompt。

當前 prompt:
{current_prompt}

案例 1: (score: 0.4)
  input: ...
  expected: ...
  actual: ...
  問題: category_match 失敗, summary_quality 低

...（案例 2-5）

請輸出改進後的完整 prompt，只輸出 prompt 本身。
```

4. 拿到 candidate prompt
5. **Eval Gate:** 用 golden set (5 組固定 test cases) 跑 candidate prompt
   - 每組算分，取平均
   - 同時跑當前 prompt 作為 baseline (或用快取的歷史分數)
   - candidate >= baseline → 通過
   - candidate < baseline → 丟棄
6. 通過 → pub 新 prompt 到 prompt-store topic

---

## Golden Set (5 組)

預先準備，hardcode 在 learner 中。範例：

**Case 1: 簡單投訴**
```
Input: "我三天前下的單到現在還沒收到，訂單編號 A12345，你們到底在搞什麼？"
Expected: {
  "category": "物流問題",
  "sentiment": "憤怒",
  "summary": "客戶反映訂單 A12345 已下單三天未到貨",
  "suggested_action": "查詢物流狀態並回覆客戶"
}
```

**Case 2: 退款請求**
```
Input: "你好，我想退掉上週買的藍牙耳機，還沒拆封，可以全額退款嗎？"
Expected: {
  "category": "退款申請",
  "sentiment": "中性",
  "summary": "客戶要求退還未拆封的藍牙耳機並請求全額退款",
  "suggested_action": "確認退貨政策並引導退貨流程"
}
```

**Case 3: 產品諮詢**
```
Input: "請問你們的年度會員跟月費會員差在哪裡？年度的有什麼額外優惠嗎？"
Expected: {
  "category": "產品諮詢",
  "sentiment": "中性",
  "summary": "客戶詢問年度會員與月費會員的差異及優惠",
  "suggested_action": "提供會員方案比較資訊"
}
```

**Case 4: 帳號問題 + 焦慮情緒**
```
Input: "我的帳號突然登不進去了，裡面還有儲值金耶！是不是被盜了？怎麼辦？"
Expected: {
  "category": "帳號安全",
  "sentiment": "焦慮",
  "summary": "客戶無法登入帳號，擔心帳號被盜及儲值金安全",
  "suggested_action": "協助帳號安全驗證並檢查異常登入紀錄"
}
```

**Case 5: 正面回饋 (edge case)**
```
Input: "上次客服小姐幫我處理得超快，想說來給你們一個好評！服務很棒！"
Expected: {
  "category": "正面回饋",
  "sentiment": "正面",
  "summary": "客戶對上次客服體驗表示滿意並給予好評",
  "suggested_action": "記錄正面回饋並轉達相關客服人員"
}
```

---

## 初始 Prompt (故意粗糙)

```
分析以下客服對話，輸出 JSON。
包含：category, sentiment, summary, suggested_action。
```

這個 prompt 故意不給範例、不限定 category 選項、不指定 sentiment 格式，讓飛輪有明確的改進空間。

---

## 專案結構

```
prompt-flywheel-poc/
├── docker-compose.yml          # Redpanda
├── requirements.txt            # Python deps
├── golden_set.json             # 5 組 test cases
├── initial_prompt.txt          # v1 prompt
│
├── app/
│   ├── gradio_app.py           # Gradio UI + OpenAI 呼叫
│   └── prompt_manager.py       # 背景 consume prompt-store
│
├── processors/
│   ├── evaluator.py            # Quix: app-log → feedback
│   └── learner.py              # Quix: feedback → prompt-store
│
└── scripts/
    └── setup_topics.sh         # 建立 Redpanda topics
```

---

## 實作順序

### Phase 1: 基礎建設 (Day 1)

1. `docker-compose.yml` 啟動 Redpanda
2. 建立 3 個 topics (`app-log`, `feedback`, `prompt-store`)
3. 寫 `prompt_manager.py`，驗證能讀寫 prompt-store topic
4. 寫 `golden_set.json`

### Phase 2: LLM App (Day 1-2)

5. 寫 `gradio_app.py` 基本版：輸入 → OpenAI → 輸出
6. 加上 pub to app-log
7. 加上從 prompt-store 讀取 prompt
8. 加上版本顯示、歷史、diff

### Phase 3: Evaluator (Day 2)

9. 寫 evaluator.py：sub app-log → 算分 → pub feedback
10. 先用簡單規則 (JSON 格式 + 欄位比對)
11. 可選：加 embedding similarity 做 summary_quality

### Phase 4: Learner (Day 2-3)

12. 寫 learner.py：sub feedback → 累積 5 條 → 觸發
13. 組裝 meta-prompt → 呼叫 OpenAI → 拿到 candidate
14. 實作 eval gate：跑 golden set，比較分數
15. 通過 → pub 到 prompt-store

### Phase 5: 整合測試 (Day 3)

16. 端到端測試：在 Gradio 跑 test cases → 觀察飛輪轉動
17. 確認 Gradio UI 正確顯示版本更動
18. 跑 2-3 輪飛輪，驗證 prompt 確實在改進
19. 準備 demo script

---

## Demo 流程 (建議)

1. 展示初始 prompt (故意粗糙)
2. 在 Gradio 上用 golden set 跑一輪，展示初始分數偏低
3. 等待飛輪：evaluator 產生 feedback → learner 觸發 → 新 prompt 產生
4. Gradio UI 自動更新版本，展示 prompt diff
5. 再跑一輪 golden set，展示分數提升
6. 重複 1-2 輪，展示持續改進
7. 討論：production 中如何加入人工 feedback、動態 eval pool、A/B test

---

## 風險與注意事項

- **OpenAI rate limit:** POC 用 gpt-4o-mini 基本不會撞到，但 learner 跑 eval gate 時會連續呼叫多次，注意間隔
- **飛輪不收斂:** candidate prompt 可能越改越差。eval gate 是最後防線，但如果 golden set 太少 (5 組)，可能有 overfit 風險。Demo 時注意觀察
- **Learner 的 meta-prompt 本身很重要:** 這個 prompt 的品質直接影響飛輪效果，需要花時間調整
- **Redpanda compacted topic 行為:** 確認 compaction 設定正確，否則 prompt-store 會保留所有歷史訊息 (其實也不是壞事，但要注意 consumer 行為)
