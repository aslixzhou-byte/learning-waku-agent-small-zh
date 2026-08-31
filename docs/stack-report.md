# 技术栈报告——验证于 2026-07-10

简报（§7）要求在动手构建之前先做一轮「先调研再验证」。结果：下面每一层都装进同一个 venv（Python 3.13、uv），并做了端到端冒烟测试。

## 选定的技术栈（以及被否掉的）

| 层 | 选择 | 已验证版本 | 被否掉及原因 |
|---|---|---|---|
| Loop / 框架 | 基于 Anthropic SDK 手写的约 150 行循环 | `anthropic 0.116.0` | PydanticAI（很好，但把我们要教的 loop 藏起来了）；smolagents（HF 实验级）；PocketFlow（图抽象，不是 loop）；经 launch-DeepResearch-Backend 引用的 LangGraph（拖进整个 LangChain 栈——见下） |
| Memory | 基于 SQLite + FTS5（stdlib）手工搭的三支柱 | Python 3.13 stdlib，FTS5 已确认 | mem0 / Letta / Zep-Graphiti 把视频要讲的（记忆整合 + 检索门控）黑盒化了。已在 README 列为生产环境替代方案 |
| Vector 升级路径 | Supabase pgvector 适配器（从 launch-agentic-rag 移植） | 可选 extras `[supabase]` | Chroma/Qdrant：又多一个要跑的服务；sqlite-vec：还在 pre-v1 alpha |
| Eval——确定性 | 对工具调用做朴素 pytest 断言 | `pytest 9.1.1` | —（这一侧绝不能使用 LLM；这正是重点） |
| Eval——LLM-as-judge | DeepEval（GEval），pytest 原生 | `deepeval 4.0.8` | promptfoo：断言很强，但 YAML 驱动 + Node CLI，在 Python 仓库里可教学性差 |
| Tracing / LLM Ops | 每次运行一条 JSONL 追踪 + OTel spans → 本地 Arize Phoenix | `arize-phoenix 17.23.0`、OTLP gRPC | Langfuse v3 自托管需要约 6 个容器（web、worker、Postgres、ClickHouse、Redis、MinIO）——对 clone-and-run 太重。Langfuse **cloud** 仍可通过同一个 OTel 环境变量开关工作 |
| Gateway | CLI REPL 默认；Telegram 长轮询可选 | extras `[telegram]`（`python-telegram-bot >=21`） | WhatsApp：Meta Business Cloud API 需要商家认证 + 公网 webhook——作为社区贡献推迟 |

## 已运行的冒烟测试（全部通过）

1. **SQLite FTS5**：虚拟表 + `MATCH` 排序查询在 stdlib `sqlite3` 里可用——无需安装。
2. **Phoenix + OTel**：`python -m phoenix.server.main serve` 约 1 秒内在 `localhost:6006` 起来；一个假的 `agent_run → retrieval_gate → tool.create_event` span 树经 OTLP（`localhost:4317`）导出，并通过 Phoenix API 出现（`traceCount: 1`）。
3. **DeepEval**：自定义确定性 `BaseMetric`（无 LLM）能测量并通过；`GEval` 能干净导入用于 judge 套件。
4. **Anthropic SDK**：0.116.0 可导入。实时工具往返待 `.env` 里的 `ANTHROPIC_API_KEY`（刻意不借用其他仓库的密钥）。

## 为什么没有扩展 launch-DeepResearch-Backend 的 loop（简报要求的）

深入探究过：它是 `open_deep_research` 的一个 LangGraph `StateGraph` fork——监督者扇出、研究专用的状态和提示词、与 LangChain 深度耦合。值得保留的*模式*（按名字索引工具的 dict、用 `asyncio.gather` 的并行分发、safe-execute 包装器）已经在这里用朴素 Python 重写进 `waku/loop/agent.py`。
