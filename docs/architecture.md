# 架构——白板，更新版

与之前视频里两张白板图（通用 Harness/Loop/Memory/LLM-Ops 那张和 Hermes 专有的那张）是同一套系统，现在每个方框都带上了文件路径。

```mermaid
flowchart TB
    subgraph GW["Gateway Interface — waku/gateway/"]
        CLI["cli.py (default)"]
        TG["telegram.py (optional)"]
    end

    subgraph RUN["Ephemeral Agent Run — everything here is rebuilt per turn"]
        WM["Working Memory — runtime/session.py<br/>SOUL.md + memory context + chat history"]
        subgraph LOOP["The Loop — loop/agent.py"]
            LLM["LLM call<br/>(loop/models.py)"]
            TOOLS["Tools — tools/<br/>create_event · save_note · send_message"]
            LLM -->|tool calls| TOOLS -->|results| LLM
        end
        WM --> LLM
        GUARD["end-loop guardrails:<br/>no-tool-call exit · max iterations"]
    end

    GW --> WM
    LLM -->|reply| GW

    subgraph MEM["Memory — waku/memory/"]
        GATE{{"retrieval_gate.py<br/>'does this turn need memory?'"}}
        PROC["procedural/ — SKILL.md<br/>how to act"]
        SEM["semantic/ — facts (FTS5,<br/>or Supabase pgvector)"]
        EPI["episodic/ — dated events"]
        CONS{{"consolidation.py<br/>'only after N new chats'"}}
        DB[("state.db — one SQLite file")]
    end

    WM -.->|every turn| GATE
    GATE -->|only if needed| SEM & EPI
    PROC -->|on keyword match| WM
    GW -->|save messages| DB
    CONS -->|distill into facts| SEM
    CONS -->|one episode| EPI
    SEM & EPI --- DB

    subgraph OPS["LLM Ops — waku/ops/ + evals/"]
        TRACE["tracing.py — 1 trace/run<br/>JSONL always · OTel → Phoenix/Langfuse"]
        DET["evals/deterministic — 0/1<br/>'did the right tool fire?'"]
        JUDGE["evals/judge — scored %<br/>'was the reply good?'"]
        RGATE{{"release_gate.py"}}
        TRACE --> DET & JUDGE --> RGATE -->|eval passed| SHIP["release: new prompt/<br/>model/config version"]
    end

    RUN -.->|every event| TRACE

    %% 讲课高对比莫兰迪：浅底 + 深字 + 粗描边
    classDef gateway fill:#C5D5E4,stroke:#2F4A63,color:#14202C,stroke-width:3px
    classDef working fill:#E8DCC8,stroke:#5C4630,color:#1F1710,stroke-width:3px
    classDef loopNode fill:#B7CBDC,stroke:#2A455C,color:#14202C,stroke-width:3px
    classDef tools fill:#C9D8E3,stroke:#35566F,color:#14202C,stroke-width:3px
    classDef gate fill:#EAD9B8,stroke:#6A5230,color:#1F1710,stroke-width:3px
    classDef memory fill:#E4CCC6,stroke:#6B3F44,color:#241618,stroke-width:3px
    classDef consolidate fill:#E6CED4,stroke:#6E404C,color:#241618,stroke-width:3px
    classDef ops fill:#D5DBE0,stroke:#2F3A42,color:#14191E,stroke-width:3px
    classDef gwBox fill:#EEF3F7,stroke:#2F4A63,color:#14202C,stroke-width:3px
    classDef runBox fill:#F6F0E6,stroke:#5C4630,color:#1F1710,stroke-width:3px
    classDef loopBox fill:#EEF3F7,stroke:#2F4A63,color:#14202C,stroke-width:3px
    classDef memBox fill:#F7EEEC,stroke:#6B3F44,color:#241618,stroke-width:3px
    classDef opsBox fill:#EFF1F3,stroke:#2F3A42,color:#14191E,stroke-width:3px

    class CLI,TG gateway
    class WM working
    class LLM,GUARD loopNode
    class TOOLS tools
    class GATE gate
    class PROC,SEM,EPI,DB memory
    class CONS consolidate
    class TRACE,DET,JUDGE,RGATE,SHIP ops
    class GW gwBox
    class RUN runBox
    class LOOP loopBox
    class MEM memBox
    class OPS opsBox
```

## 值得借鉴的设计决策

- **检索之前的门禁**（不是每轮都检索）：用小模型做裁判，回答「这条消息需要用到用户的记忆吗？」——省延迟，更重要的是避免无关记忆带偏答案。
- **记忆整合是批量的**（「每 N 轮之后」），与回复路径异步，且不会丢数据：如果摘要器失败，聊天日志保持未整合状态。
- **确定性评测和 judge 评测永不混用。** 一个是单元测试，另一个是带分数的观点。发布门禁要求前者 100% 通过、后者达到阈值。
- **每一层都有朴素的默认实现和一个有文档的升级路径**——FTS5 → pgvector、mock 日历 → Google Calendar、JSONL → Phoenix/Langfuse。默认实现永远零注册即可用。

## 这刻意不是什么

不是框架，不是多 agent，不是生产环境。它是可读的蓝图——OpenClaw 和 Hermes 是产品；这份文档是一个下午就能读完、解释它们的读物。
