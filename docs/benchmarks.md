# 模型对比——Waku 基准测试组

Waku 如何比较模型、每个测试测什么、以及怎么读结果。这是**Eval / LLM-Ops** 支柱横着用了一次：不是随时间给同一个 agent 打分，而是让*同一个任务同时穿过多个大脑*，并给结果打分——不只是看账单小票。

> **诚实的前提说明。** Compare（dashboard `#compare`）和 `scripts/shootout.py` 测的都是*在同一个共享框架上实时完成任务*。它们**不**复现标准化榜单（SWE-bench、τ-bench、Terminal-Bench、GPQA）——那些需要各自的数据集和官方框架。每个模型的公开榜单数值在 [§6](#6-published-benchmarks-reference)。我们的基准测试组是**本地的、可复现的镜像**：在隔离沙箱里看每个模型做同样的真实助手任务，然后给「是否真的做成了」打分。

---

## 0. 给视频——评测与打分主线

本文档兼作分镜清单。视频的主轴是**评测与打分**，不是「模型 X 最强」：重点在于*你怎么做决定*——诚实地、带着证据。你会在镜头前跑的测试清单是 [§3](#3-battery-sections)——即基准组，按组分好，可以一次拍一组。建议的主线：

1. **朴素的记分板（铺垫）。** 展示Compare用一条 prompt 跑 11 个模型。速度、token、成本。然后转折：*「这只能告诉我谁又便宜又快——不是谁真的把活干了。」*（正是这个缺口催生了整支视频。）
2. **逐轴打分。** 介绍四个轴（[§1](#1-the-four-axes)）：速度、成本、**完成度**（工具是否真的触发 / 事件是否真的建了）、**质量**（K3 做中立裁判）。强调 Completion 是*确定性的*——没有玄学，就是把 τ-bench/SWE-bench 的思路在本地做了一遍。
3. **硬案例（便宜的模型在这里翻车）。** 跑基准组 A 的四个硬案例（[§3.A](#a-agentic-tool-calling--the-assistants-real-job)）——过度积极、数量精确、完整度、状态感知。这是最值钱的片段：模型回答得很流利，*却仍然过不了检查清单*。
4. **K3 当裁判。** K3 当众给全场模型的转录打分，包括它自己（[§5](#5-the-judge--k3-as-neutral-referee)）。由赞助商的模型来当裁判就是噱头——诚实地展示，不藏着。
5. **揭晓——成本 vs 质量。** Pareto 散点图（[§1](#1-the-four-axes)）：「opus 比 gemini-flash 贵 20 倍——它好 20 倍吗？」答案是一张*图*，它就是封面图。
6. **（可选）编程回合。** 把真实编码任务跨模型委托给 pi（[§3.B](#b-coding--delegate-to-a-sub-agent)）——以测试是否通过来打分。

下面所有内容都是这条主线所依据的参考资料。

---

## 1. 四个轴

每一场比赛列都会产出四个独立信号。成本/速度容易读；后两个是收据无法体现的「到底做成没有」。

| 轴 | 回答什么 | 来自哪里 | 计算成本 |
|------|-----------------|---------------------|-----------------|
| **速度** | 完成所需的墙上时钟时间 | 来自 loop 的 `latency_ms` | 免费 |
| **成本** | 尝试所花的美元 | token × 每模型价格表 | 免费 |
| **完成度** | *它做成活了没有？* | 对工具调用 + 沙箱状态 vs 任务预期结果的确定性检查 | 免费 |
| **质量** | *回答有多好？* | LLM-as-judge（K3 做中立裁判）给转录打 0–10 分 + 理由 | 每列 1 次 judge 调用 |

Compare想讲的故事：**把 Cost 对着 Completion/Quality 画出来。** 「Opus 比 gemini-flash 贵 20 倍——它完成任务的能力也好 20 倍吗？」两个完成度得分相同、成本却差 20 倍的模型，就是整支视频的全部要点。

---

## 2. 任务怎么打分（约定）

基准组案例放在 `evals/dataset.jsonl` 里，每行一个 JSON 对象。同一个文件喂给三个消费方，所以写一次案例，到处打分都一致：

- 确定性评测层（`evals/deterministic/`，pytest，0/1），
- CLI 对战（`scripts/shootout.py`，跨模型表格），
- 实时 Compare的 **Completion** 列。

**Completion 字段**（除 `id` + `input` 外均可选）：

| 字段 | 含义 |
|-------|---------|
| `input` | 发给每个模型的用户消息 |
| `expect_tool` | 必须触发的工具（`null` = 绝不能调用任何工具） |
| `expect_in_args` | 该工具参数里必须出现的子串（不区分大小写） |
| `expect_min_tool_calls` | 工具调用总数的下限（多步任务） |
| `setup_fact` | 运行前预载进沙箱的一条记忆 |

Completion 得分 = 一个 case 里被满足的预期所占比例（0.0–1.0）。这就是确定性轴：没有 judge、没有玄学——*正确的工具是否用正确的参数触发了，并且（多步任务）次数够不够。*

**Quality** 是独立且可叠加的：judge（§5）读最终转录，并按评分标准打分。一个模型可以完成任务（Completion 1.0）却因为回答笨拙或冗长而得到平庸的 Quality 分——反之，跳过工具却流畅的回复会得 Quality 高 / Completion 低。刻意把两者分开；把它们合并恰恰会藏起我们想看到的失败。

---

## 3. 基准组分区

基准组按组分好，可以一次比一组。标 **[seeded]** 的 case 已存在于 `evals/dataset.jsonl`；**[proposed]** 是接下来要加的。

### A. Agent 式工具调用——助手的真实工作

在 Waku 旗舰工具（`create_event`、`save_note`、`send_message`、只读的 `search_web`）之上做多步编排。这是 K3 天生要赢的轴（它的卖点就是 Terminal-Bench / agent 式工具调用）。

| id | 任务 | 预期结果 |
|----|------|------------------|
| `schedule-basic` **[seeded]** | "Schedule a coffee with Alex next Tuesday at 9am" | `create_event`，标题~alex，开始~09:00 |
| `schedule-applies-memory` **[seeded]** | (事实：Alex 更喜欢早上) "Book a catch-up with Alex on Friday" | `create_event`，应用了该事实 |
| `remember-preference` **[seeded]** | "Remember that Alex prefers morning meetings" | `save_note`，内容~morning |
| `draft-message` **[seeded]** | "Send Alex a message that the demo moved to Friday" | `send_message`，正文~friday |
| `pokemon-team` **[seeded]** | "…search picks, remember Pikachu is my starter, schedule two training sessions" | ≥3 次工具调用：search + save_note + 2× create_event |
| `worldcup-final` **[seeded]** | "…search the result, remember who won, draft a message to Raj" | ≥3 次工具调用，send_message to~raj |
| `chitchat-no-action` **[seeded]** | "I might grab coffee with Alex sometime, we'll see." | `expect_tool: null` ——自言自语不是命令；绝不能安排日程 |
| `exact-count-sessions` **[seeded]** | "Block three 25-minute focus sessions tomorrow morning" | ≥3 次 `create_event` ——数量精确性，弱模型只做一个 |
| `remember-and-book` **[seeded]** | "Remember I'm vegetarian, then book dinner with Sam Thursday 7pm" | ≥2 次调用：必须同时做 save_note + create_event(sam) |
| `read-before-write` **[seeded]** | "Check my calendar for a free 30 min this afternoon and schedule a walk" | ≥2 次调用：先 list_events（读状态）再 create_event |

最后四个是*硬*案例，各有各的失败模式：**过度积极**（对自言自语也动手）、**数量精确**（精确做三个，不是一个）、**完整度**（两半都做，不只做记得住的那半）、**状态感知**（盲目安排前先读日历）。这正是只看流畅度的 judge 会漏掉、而 Completion 得分能抓住的。

### B. 编码——跨模型，走 pi   **[built — CLI]**

Waku 是编排者；**pi** 是编码承包商——但对*编码*基准，我们把 pi 指向每个**参赛者**的模型，于是用一个固定框架给每个大脑试镜。编码 case 会初始化一个沙箱、把任务交给 pi，然后通过**运行产出代码的 `verify` 命令**来打分——SWE-bench 风格（测试通过、exit 0），绝不去读回复。

case 放在独立文件 `evals/coding.jsonl`（与 agent 数据集分开，所以永远不会走工具调用层），含 `input`、可选的 `files`（预载进沙箱）、以及 `verify`（以退出码作为得分的命令）：

| id | 任务 | verify |
|----|------|--------|
| `code-fizzbuzz` **[seeded]** | "Create fizzbuzz.py with fizzbuzz(n)…" | 导入它，断言 Fizz/Buzz/FizzBuzz/str(n) |
| `code-bugfix` **[seeded]** | 预载一个 `add()` 有错的 `calc.py` → "fix it, don't touch check.py" | 运行 `check.py` |

跑起来：
```bash
make shootout-coding RUNS="kimi:kimi-k3 anthropic:claude-opus-4-8"
```
pi 原生支持我们锁定的每个 provider（anthropic、openai、google/gemini、**moonshotai/kimi**、xai/grok、zai/glm）——runner 把 Waku 的 provider id 映射到 pi 的，并用 `--api-key` 传入密钥，所以 K3 和全场在完全对等的前提下比赛。实时已验证：opus-4-8 和 kimi-k3 都解出了 `code-fizzbuzz`（以真实测试执行打分）。打分器在 [`waku/ops/coding_eval.py`](../waku/ops/coding_eval.py)。

**实时 Compare里也一样——走 LOOP，而不是绕过它：** 打开 **"coding (pi)"** 开关然后比赛。这会为比赛注册 `delegate_task`，所以每张卡片都跑**完整框架**（门禁 → 记忆 → 工具），由模型*自行决定*调用 `delegate_task`，它会在**那张卡片自己的模型**上生成一个 pi 子 agent 来写并运行代码。它是自主的——loop 依次跑 model → delegate_task → model 收尾，不需要停下来等——而且卡片展示真实凭据：门禁徽章、`delegate_task` 工具芯片、token 和成本（loop 自己的；pi 的内部 token 不采集）。自由格式的 prompt（"build snake and run it"）也行，因为 pi 有 bash 工具，所以"run it"在委托的子 agent 内部完成。（推理模型在这里很慢——kimi-k3 当 loop 大脑 + pi 可能要几分钟；拍 2-3 个模型就行。）

**代码落在哪 + 自动运行：** 零散编码任务不会消失在临时目录里——`delegate_task` 把它存到带日期、自解释的工作区（`./waku_workspace/<date>/<time>-<model>-<slug>/`，git-ignored），并附 `MANIFEST.md`（日期、模型、任务、文件、运行结果）、pi 写的文件、pi 转录和 `run.log`。pi 完成后，框架会**自动运行**入口脚本（无头、捕获输出、30s 超时），把结果回喂给 loop，所以模型能看到自己的代码是否真的跑起来。配置：`WAKU_WORKSPACE`（根目录）、`WAKU_DELEGATE_AUTORUN=0`（禁用）、`WAKU_AUTORUN_TIMEOUT`。见 [`waku/tools/workspace.py`](../waku/tools/workspace.py)。

### C. 记忆与上下文

| id | 任务 | 预期结果 |
|----|------|------------------|
| `recall-across-session` **[proposed]** | 存一条事实，新会话，问它 | 事实被检索进工作记忆 |
| `retrieval-gate-negative` **[proposed]** | 存一条事实后再问无关问题 | 门禁**不**拉取该事实（hero-1 行为） |

### D. 推理 / 知识——仅 judge

没有工具；用 K3 judge 直接给回答本身打分。几条 GPQA 风格或「精确解释 X」的 prompt，打 0–10 分。它们的存在是为了展示 Quality 轴独立于 Completion 变化。

---

## 4. 子 agent 生成——已实现（`delegate_task` → pi）

是的，Waku 已经支持 Hermes / Claude-Code 风格的多 agent 生成。它作为 `delegate_task` 放在 [`waku/tools/experimental.py`](../waku/tools/experimental.py)，**默认关闭**——设 `WAKU_EXPERIMENTAL=1` 才会注册。

- **它是什么：** 架构白板上的「Sub-Agents」方框，接上了真货。它通过 pi 的无头打印模式（`pi -p "task"`）把编码任务交给 **pi**（Mario Zechner 极简开源编码 agent，`github.com/earendil-works/pi`）。
- **分工正是教学点：** Waku 是编排者（记忆、工作记忆拼装、人的上下文、发布门禁）；pi 是专职承包商（read / bash / edit / write）。Waku 雇人；pi 写码；Waku 的门禁检查成果。
- **诚实契约：** 工具的返回字符串如实说明发生了什么（done / failed / timed-out / pi-not-installed）；完整 pi 转录落到 `.waku/outbox/delegate-*.log`。
- **要求：** `npm install -g --ignore-scripts @earendil-works/pi-coding-agent`。
- **这是基准组 B 的地基**——每个编码 case 都是一次 `delegate_task`，以 pi 的输出是否通过测试来打分。

三个兄弟方框仍是诚实的骨架（返回 "coming soon"）：`run_command`（Terminal）、`browse_web`（Browser）、`schedule_task`（Cron）——每个都需要真实沙箱 + 安全面之后才能上线。

*源码里已经记下的 v2 想法：* 跑 `pi --mode json`，把它的逐轮事件流进 dashboard 的 Loop 标签，这样一次委托的编码运行能和普通 loop 一样动态展示。

---

## 5. 裁判——可切换、中立的裁判员

Quality 轴通过 [`waku/ops/judge.py`](../waku/ops/judge.py) 给每条回复打 0–10 分 + 一行理由。裁判可以**从Compare切换**（"grade" 开关旁边的下拉框），默认是 **gpt-5.6-sol**。

**为什么不用 K3 当裁判：** 不能用 K3 来测 K3——让参赛者给自己打分没有可信度，而且（我们实测遇到过）K3 当时也在*比赛*，所以同时给每一列打分把它自己的端点打爆、429，导致大部分分数空白。裁判应该选一个**没在比赛**的模型。gpt-5.6-sol 是自然之选：一个强推理模型，在这里当*参赛者*很吃亏（它在 chat 端点上不能调工具），但当*裁判*很合适（打分纯属文本）。任何 provider 都行——Waku 的 OpenAI 兼容客户端给裁判和 anthropic 线协议相同的接口。

**分数意味着什么（上镜时说这段）：** 0–10 衡量回复在多大程度上满足了请求——正确、诚实、简洁。9–10 完全满足；5–8 有轻微欠缺；0 是幻觉或声称做了没做的动作。

**关键的公平衡量修复：** judge 只看到回复的*文本*，所以一句如实的"I saved that"看起来像幻觉、打了 0 分，尽管 `save_note` 真的触发了。现在会把**实际运行过的工具列表**作为 ground truth 交给 judge，所以有真实工具调用支撑的真实动作能正确得分（已验证：同一回复——没有工具上下文打 0 分，有则打 10 分）。

它所对应的最佳实践：对开放式质量用 MT-Bench / Chatbot-Arena 式的 LLM-as-judge，对任何有可检查终态的东西配以程序化结果检查（τ-bench / SWE-bench 风格）。我们两者并行。

---

## 6. 公开基准参考

每个置顶模型的标准化榜单数值，仅供参考——中立表述，不做我们自己的排名。来源是第三方追踪器 + 厂商卡片；上镜引用前请核实。

**Kimi K3**（2.8T 参数、1M 上下文、$3/$15 每 Mtok）：
- Terminal-Bench 2.1：**88.3** · Program Bench：**77.8** · FrontierSWE：**81.2**（KimiCode 框架）· DeepSWE：**67.5**
- GPQA Diamond：**93.5%**（已发布开源权重中最佳）· BrowseComp：**91.2%**
- agent 式工具调用综合排名约 #4 / 119，均值 66.6

*(每确认一个就把其余置顶模型的卡片补在这里——Opus 4.8、GPT-5.6 Sol、Gemini 3.1 Pro、Grok 4.5——遵守同样的来源纪律。)*

来源：BenchLM、NxCode、OfficeChai、Trilogy AI（链接见聊天记录）。这些是**外部**基准；我们的基准组（§3）是可复现的本地补充，不是替代品。

---

## 7. 怎么跑

```bash
# CLI shootout — deterministic Completion across models, prints a markdown table
make shootout RUNS="kimi:kimi-k3 anthropic:claude-opus-4-8"
#   → also writes a timestamped .md + .json to .waku/shootout/

# Live arena — race every pinned model on one prompt, watch it stream
make dashboard            # localhost:7777 → Compare tab

# Judged answer quality (never let a contestant judge itself)
make eval-judge
```

**读记分板：** 累计表跨比赛汇总 Cost / Time / Tokens，并（一旦建好）显示每个模型的 Completion 和 Quality。点任意列头排序；通常最值得看的是「真正完成里最便宜」的那行，而不是整体最便宜（每场比赛都报错的模型是 $0.00 且毫无用处）。

---

## 8. 最佳实践，以及我们还欠着什么

**市场上**，agent 式模型对比分成两大家族，正经的做法两者都用：

1. **程序化结果检查**——给*终态*打分，不是给文笔打分。SWE-bench（补丁能否应用、测试是否通过）、τ-bench（工具 agent 是否把数据库留在正确状态）、Terminal-Bench、BFCL（函数调用准确率）。客观、可复现、便宜。→ **我们的 Completion 轴。**
2. **LLM-as-judge + Elo**——用于没有唯一正确答案的开放式质量。MT-Bench、Chatbot Arena。主观但可扩展。→ **我们的 Quality 轴。**

**Waku 已有的：** case 格式（`dataset.jsonl`）、确定性打分、跨模型 CLI（`shootout.py`）、judge 框架，以及 Speed/Cost/Tokens 的实时 Compare。

**还欠着什么（本文档点名的缺口）：**
- ~~Completion 列接入实时 Compare~~——**完成**：在已知基准 case 上跑一场，现在会实时给每一列打分（绿色 "solved" / 红色 "failed · why" 徽章 + 一个 "solved" 记分板列），经由 [`waku/ops/scoring.py`](../waku/ops/scoring.py) 里与 `shootout.py` 共享的同一个打分器。
- ~~基准组 B（编码）+ 跨模型 pi~~——**完成，CLI *和*实时 Compare都行**：`make shootout-coding` 出表格；在Compare里，"coding (pi)" 开关让每张卡片在自己的模型上走 pi、终端实时流式输出，以测试打分。
- ~~Compare里的 Quality 列（K3-as-judge）~~——**完成**："grade with K3" 开关给每条回复打 0-10 分（[`waku/ops/judge.py`](../waku/ops/judge.py)）；每列徽章 + 一个 "K3 grade" 记分板列。
- ~~成本 vs 质量可视化~~——**完成**：记分板以成本-vs-(quality|completion) 散点图打头——便宜又好 = 左上角。
- 剩余：实时 Compare里的编码列；随着视频脚本定稿，再补更多基准 case。

---

## 9. Dry run——拍摄前完整预演一遍

每个轴的完整彩排。把每条 prompt **逐字**复制进Compare的消息框——Compare按精确文本把它匹配到基准 case 并自动打分（打错一个字 → 它照常比赛但 Completion 显示 "—"）。

### 准备
1. `make dashboard` → 打开 `localhost:7777` → **Compare** 标签。
2. 选你要拍的模型（点击芯片）。全阵容就选全部 11 个。
3. 点记分板上的 **clear** 从零开始。
4. 打开 **"grade with K3"**（右上角，Race 旁边），这样每列也会有 Quality 分。（每列多花一次 K3 调用——预期如此。）

### 第一幕——简单案例（人人都该过）
逐条粘贴、点 Race，看各列填满：

- `Schedule a coffee with Alex next Tuesday at 9am`
- `Remember that Alex prefers morning meetings`
- `Send Alex a message that the demo moved to Friday`
- `What is the capital of France?`  ← 诚实的**无工具**案例：好模型不需要调工具就能回答（绿色 "solved" = 它正确地保持不动手）

### 第二幕——硬案例（便宜的模型在这里翻车——最值钱的片段）
- `I might grab coffee with Alex sometime, we'll see.`  ← 绝不能安排日程（过度积极陷阱）
- `Block three 25-minute focus sessions tomorrow morning`  ← 必须建**三个**事件
- `Remember that I'm vegetarian, then book dinner with Sam this Thursday at 7pm`  ← 必须**两件都做**
- `Check my calendar for a free 30 minutes this afternoon and schedule a short walk`  ← 必须**先读**再写

留意：模型回复得很流利却拿到红色 **"failed · <why>"** 徽章。这就是整个论点在屏幕上呈现。

### 第三幕——多工具展示
- `Build me a Kanto starter team around Pikachu: search current competitive picks for a balanced six, remember that Pikachu is my starter, and schedule two team-training sessions this week`
- `Search for the result of the Spain vs Argentina World Cup final, remember who won, and draft a message to Raj about watching the highlights together`

### 第四幕——揭晓
滚动到 **Scoreboard**：顶部的成本-vs-质量**散点图**（便宜又好 = 左上角），然后是表格——点表头按 **solved**、**K3 grade** 或 **total cost** 排序。这就是「opus 贵 2 倍就好 2 倍吗？」的那一镜。

### 第五幕——编码回合（终端，还不是 dashboard）
```bash
make shootout-coding RUNS="kimi:kimi-k3 anthropic:claude-opus-4-8 gemini:gemini-3.5-flash"
```
每个模型的 pi 写真实代码，以测试是否通过打分。报告保存到 `.waku/shootout/coding-*.md`。

### 可选——可复现的 CLI 表格（全部 agent 案例、全部模型）
```bash
make shootout RUNS="kimi:kimi-k3 anthropic:claude-opus-4-8 gemini:gemini-3.5-flash openai:gpt-5.3-chat-latest xai:grok-4.5"
```
加 `--trials 3` 得到稳定的通过率（工具调用具有不确定性）；保存 markdown + json 报告到 `.waku/shootout/`，任何人用自己的密钥都能复现。

### 要排练绕开的坑
- **gpt-5.6-sol** 每场比赛都故意报错（推理模型，在 /v1/chat 上不能调工具）——留它在阵容里以展示诚实的报错，或者去掉这个芯片。
- **grok** 需要 xAI 额度，否则会 403。
- 同时给全场打分可能让 K3 端点 429；某列的打分会显示 "—"（内置一次重试）。如果上镜需要，就重跑那一场。
- **kimi-k3 很慢**（推理）——它的列最后才完成；记分板在模型落地时逐个纳入，所以不会被卡住等它。

---

## 10. 指标——记分板每一列是什么意思（以及精确算法）

上镜图例。每个数字都在一处、以一种方式计算，并**与手算核对过**（下面有算例——所有字段都对得上）。

| 列 | 含义 | 精确公式 | 好 = |
|--------|---------------|---------------|--------|
| **solved** | Completion——它真的把任务做成了吗 | `passed / scored`，按 case 检查清单（工具对、参数对、调用次数够） | 尽可能接近 `scored/scored` |
| **K3 grade** | Quality——回复有多好 | 被判定回复的 kimi-k3 0-10 分的均值 | 越高越好（7+ 算强） |
| **races** | 这个模型跑了多少次 | 跨所有比赛的该模型行数 | —（分母） |
| **ok** | 无报错的运行 | `ok / races`（一次报错 = 模型连跑都没跑起来） | `races/races` |
| **total time** | 累计墙上时钟时间 | **成功**运行下的 Σ `latency_ms` | 越低越好 |
| **in tok / out tok** | 累计提示 / 补全 token | 成功运行下的 Σ `tokens_in`、Σ `tokens_out` | — |
| **total tok** | 输入 + 输出 | `in tok + out tok` | 同样工作量下越低越好 |
| **rate $/M** | 模型的官方定价 | 每百万 token 的 `$in / $out`（逐模型，已核实） | —（参考） |
| **total cost** | 累计美元 | 成功运行下 Σ `(tokens_in × rate_in + tokens_out × rate_out) / 1,000,000` | 同样质量下越低越好 |

### 决定边角案例的规则（上镜时把这些大声说出来）

- **每次读取都会用 token 重算成本**，所以一次定价修正也会修复过去的比赛——数字永远不会过期。
- **输出 token 的成本是输入的 3-5 倍**——这就是 in/out 分开的原因。一个"便宜"但话多的模型可能比一个更贵但简洁的模型花得更多。
- **报错的比赛计入 `races` 并拉低 `ok`，但对** token、成本或 completion **毫无贡献**。只报错的模型是 `$0.00`——而且无用。看「真正*完成*里最便宜」的，而不是整体最便宜的。
- **只有已知基准 case 的 prompt 才会得到 `solved` 分**（精确文本匹配）；自由格式的 prompt 照常比赛但显示 `—`。
- **只有打开 "grade with K3" 时 `K3 grade` 才会出现**；未被判定过的比赛显示 `—`。

### 算例（这是对算法的复核）

两场比赛、两个模型、已知 token 数：

| model (rate) | race 1 | race 2 | → solved | K3 grade | total tok | total cost |
|---|---|---|---|---|---|---|
| kimi-k3 ($3/$15) | 1000 in / 500 out · passed · q8 | 2000 in / 1000 out · failed · q6 | **1/2** | **7.0** = (8+6)/2 | **4500** | **$0.0315** = (3000·3 + 1500·15)/1M |
| gemini-3.5-flash ($1.5/$9) | 1000 in / 200 out · passed · q5 | **errored** | **1/1**（报错的比赛不计分） | **5.0**（只有第一场被判定） | **1200** | **$0.0033** = (1000·1.5 + 200·9)/1M |

用这些输入跑 `aggregate()` 能精确重现每个加粗数字——记分板算法是正确的。（随时可以用添加本节那个提交里的算例脚本复核。）
