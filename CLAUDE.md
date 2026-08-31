# waku-agent —— 工作约定

**Waku** —— 一个本地优先的个人助理，演示每个严肃 agent 背后的四大支柱：
Harness（框架）、Loop（循环）、Memory（记忆）、Eval/LLM-Ops（评估）。它最初是一个
一下午就能读完的教学仓库，现在正成长为一个完整的开源助理（下一个 Hermes / OpenClaw）。
每一项改动的标准：**清晰、诚实、新人都能跟上的代码** —— 每一根支柱单独拿出来都易读。
项目会越来越大，但绝不能越来越乱。新的范围是欢迎的，只要它自包含、有测试、可读；
为了复杂而复杂则不行。

## 架构图（文件 ↔ 图框）

- `waku/gateway/` —— cli、voice（唤醒词）、telegram。网关只搬运文本。
- `waku/runtime/session.py` —— 工作记忆组装（SOUL.md + 记忆 + 历史）
- `waku/loop/agent.py` —— 主循环；`loop/models.py` —— 可插拔 provider，两种线格式
- `waku/tools/` —— create_event / save_note / send_message（仅旗舰任务）
- `waku/memory/` —— 语义（FTS5）/ 情景 / 程序性（SKILL.md）+
  `retrieval_gate.py`（高光 1）+ `consolidation.py`（每 N 次交流）
- `waku/ops/` —— 追踪（JSONL + OTel）、dashboard（localhost:7777）、release_gate、
  `compare_history.py`（Compare 竞技场自己的 JSONL 记分板 —— 绝不碰 state.db）
- `evals/deterministic/`（0/1，pytest）vs `evals/judge/`（DeepEval，打分）—— 绝不混用
- 运行时状态在 `.waku/`（state.db、calendar.ics、outbox/、traces/）—— 已 gitignore

## 规则

- **要简洁。** Sean 要短回复：先给答案，砍掉开场白和复述。几行胜过一堵文字墙。只有他要求细节时才展开。
- **绝不未经询问就清空运行时数据，每次都要问。** `scripts/demo_seed.py` 以及任何会清空 `.waku`
  （记忆、日历、聊天日志、追踪，或 `usage.jsonl` 花费账本）的东西，都必须在*每次运行前*
  提出并得到用户明确批准。许可绝不从上一次运行顺延。脚本会先备份，但恢复很麻烦 ——
  先问，等一个明确的「是」，再运行。正因如此，没有 `--yes` 标志它就拒绝做任何事。
- **版本控制 —— 每个里程碑同轮 commit 并 push。** 一旦某改动生效（测试通过 / 实测验证），
  就用详细的信息提交（subject = 做了什么，body = 为什么 + 它经历了什么），并在继续前
  `git push origin main`。绝不要在回合或会话结束时留下未提交的改动 —— 仓库必须始终能从
  GitHub 追踪，未提交的工作以前就因切分支而丢失过。用 `/ship` 技能。如果一个会话里落了
  多个里程碑，每个都作为各自的逻辑提交。
- **push 前先过门禁**：`make gate`（确定性必须通过；judge 带 key 运行）。
  发现线上 bug 时，修它并在 `evals/deterministic/` 加一个回归用例。
- **任何 UI 界面都不得用 emoji**（dashboard、CLI 输出、README 正文）。
- **未经讨论不新增依赖** —— 核心是标准库 + anthropic/openai。
  可选功能放在 extras 后面（`[voice]`、`[telegram]`、……）。
- **范围**：日程是旗舰教学任务，但项目正朝完整助理发展。新能力（provider、工具、网关、
  集成）在自包含、有测试、且让核心保持可读时是欢迎的。只拒绝那些让系统变糊、或让默认路径
  膨胀的复杂 —— 优先可选的 extras。
- 文档中对 provider 保持中立措辞（Anthropic、OpenAI、Gemini、DeepSeek、Kimi、GLM、
  OpenRouter）—— 不排名，不用「开源 vs 闭源」的框法。

## 命令

`make run` · `make voice` · `make dashboard` (7777) · `make trace` (6006) ·
`make eval` · `make gate` · `make lint` · 测试在 `evals/` 下，而非 `tests/`
