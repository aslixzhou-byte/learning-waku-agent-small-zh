# 演示 / 拍摄清单

按顺序在镜头前走一遍的工作流。每个节拍都写明确切的提示词、它证明了什么、该看哪里、以及是否已通过 dry-run 验证。测试过程中请持续更新。

## 起飞前

- [x] Provider = `anthropic`（流式体验最好；Gemini 会打断多轮工具调用）—— Settings
- [x] 在 Settings 页粘贴免费的 `TAVILY_API_KEY`（世界杯节拍要用）
- [x] 干净的精选状态——`python scripts/demo_seed.py --yes`（清除 Loop/Tools 追踪 + Ops 评测历史；除非加 `--reset-spend`，否则保留 `usage.jsonl` 花费账）。没有 `--yes` 会拒绝执行——它是破坏性的。
- [x] 在自己机器上运行 `waku dashboard`（若设置了 token 也会启动 Telegram）→ 在真实浏览器打开 `localhost:7777`

## 各节拍

| # | 节拍（支柱） | 说这句 | 看哪里 | 已验证 |
|---|---|---|---|---|
| 1 | 驾驶舱导览（Harness） | —（四处点击） | Overview：统计、门禁条、可点击图表 | [x] |
| 2 | 网关（Harness） | 从 `make run` **和**浏览器里聊天 | Gateway 标签会为每条消息打 `cli` / `dashboard` 标记 | [x] |
| 3 | Loop + 流式 | *"Schedule a tennis game with Raj this Saturday at 8am"* | 回复流式输出；LOOP 框脉冲闪烁；Loop 标签 `iter 2` | [x] |
| 4 | 日历读取 | *"What's on my calendar today?"* | `list_events` 触发；答案来自 `state.db` | [x] |
| 5 | 检索门禁（Memory） | *"When am I swimming with Sergey?"* 然后 *"what's 12 × 8?"* | 门禁 retrieve vs skip；Overview 条；Ops 决策 | [x] |
| 6 | 记忆自管理 | *"Remember Raj prefers morning tennis"* | `save_note`；Memory ▸ Semantic + `MEMORY.md` 更新 | [x] |
| 7 | **多工具循环（招牌镜头）** | *"Search the World Cup games still left and add each to my calendar"* | Loop 标签 `iter 8`：`search_web` × N → `create_event` × N | [x] |
| 8 | 记忆整合（Memory） | 聊到超过 N 轮 | Memory ▸ Consolidation；一条新情节 + 蒸馏出的事实 | [x] |
| 9 | Telegram 网关 | 用手机给 bot 发消息 | Gateway 标签显示它被打上 `telegram` 标记 | [x] |
| 10 | **语音** | `waku voice`——免提，说 "waku waku, …"（或点击坞站麦克风） | WAV → 本地 Whisper；落在独立的 `voice` 会话里 | [x] |
| 11 | Eval / LLM-Ops（第二高光） | 在终端跑 `make gate` | 打印 `GATE OPEN`；Ops ▸ Eval 历史多出一行 | [x] |
| 12 | 花费账本 | （只是看看） | Ops：累计花费/token、按天——重置后仍在 | [x] |
| 13 | **Database 标签页** | 点击每个表标签；在 **SQL console** 里跑一条查询 | 每张表的 schema（靛蓝表头）+ 行；`SELECT` 返回实时数据（且持久化） | [x] |
| 14 | Ops 走读 | （只是看看） | Ops：评测历史表、每轮门禁决策、最慢的轮次、内嵌 JSONL 追踪 | [x] |
| 15 | Markdown 聊天 + Gateway 收件箱 | （加分项） | 回复渲染加粗/表格/列表；Gateway = 按渠道打标的会话收件箱，telegram 实时在线 | [x] |

## 状态：15 个节拍全部 dry-run 验证通过 ✅

上面所有内容在实时 dashboard 上都可用。三条真实网关已验证（web / telegram / voice），四大支柱齐全，两个高光时刻都在。剩下的只是可选的打磨，不是测试——比如把 README 从头到尾过一遍作为上镜脚本，以及用 `demo_seed.py` 做一次干净拍摄。

## git 历史在哪

上面每个功能都是 `main` 上的一个提交，提交信息聚焦「为什么」——`git log --oneline` 就是变更日志。关键的：流式、`search_web`（世界杯循环）、`list_events`（日历读取）、常驻花费账本 + `MEMORY.md`、来源打标的 Gateway、coming-soon 骨架。

## 两次拍摄之间重置

`python scripts/demo_seed.py --yes` ——为干净拍摄清除记忆/日历 **以及** Loop/Tools 追踪 + Ops 评测历史，并且先把整个 `.waku` 备份。除非你传 `--reset-spend`，否则 `usage.jsonl` 花费账本**保留**（它是永久记录）。它从不删除 db 文件，所以运行中的 `waku dashboard`/`waku telegram` 不受影响。没有别的东西会清你的数据。
