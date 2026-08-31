# 向 Waku 贡献

Waku 起初是一个一个下午就能读完的教学仓库，如今正成长为一个完整的本地优先助手——下一个 Hermes / OpenClaw，代码量却只有其 1/100。我们真诚欢迎各种贡献。项目会越来越大；唯一绝不能变的是*变糊涂*。

**每份 PR 的门槛：** 清晰、自包含、有测试。新同学应当能打开你改动的文件并看懂它在做什么。新能力很棒——把系统怎么工作藏起来的复杂度，才是我们要挡回去的。

## 最简单的贡献：一个 skill（不需要 Python）

1. 把 [`skills/TEMPLATE.md`](skills/TEMPLATE.md) 复制到 `skills/community/<your-skill>/SKILL.md`
2. 填 `name` + `description`（Agent Skills frontmatter）和正文
3. 本地测试：`python scripts/validate_skills.py`，然后聊一句——命中时你的 skill 就会加载
4. 开 PR。CI 会跑同一个校验器。

任何人随后都能立即试用你的 skill：
`waku skill install <link to your SKILL.md>`

## 代码贡献

真正能加分的好位置：

- **模型提供方**（`waku/loop/models.py`）：多数模型暴露 OpenAI 或 Anthropic 兼容端点，所以新增 provider 通常就是一行 `PROVIDERS` 条目——不需要新写线协议。在 dashboard 加一行定价，再往 `evals/deterministic/test_providers.py` 加一个 case。
- **网关**（`waku/gateway/`）：为新渠道（WhatsApp、Discord、Slack、email）收发文本。保持单文件；CLI 网关是参考实现。
- **记忆存储**（`waku/memory/semantic/`）：对齐 `SqliteFactStore` 的 `add`/`search` 接口。Supabase 适配器是参考。
- **工具**（`waku/tools/`）：一个 agent 能调用的新能力。照着 `calendar.py` 和 `new-tool` skill 来——schema、安全执行、诚实输出、加一条确定性评测。

两条让贡献可以安全合入的规则：

- **测你加的东西。** 每处行为改动都要在 `evals/deterministic/` 里有一条确定性评测（0/1，不联网）。发现 bug 就加上能抓到它的 case。
- **重型或可选依赖放到 extras 后面**（`[voice]`、`[telegram]`、`[voice-neural]`、……），绝不进默认安装。没有讨论就不加新的核心依赖。

推送前跑一遍门禁：`make gate`（deterministic 必须全过；有 key 时 judge 评测也会跑）。`make lint` 也跑一下。CI 在每份 PR 上跑门禁——必须全绿才能合入。

## 范围——我们会礼貌地说不的东西

我们欢迎成长；拒绝的是**把核心搞糊涂的复杂度**：把 loop 藏起来的框架、让所有人默认路径变臃肿的改动、或没法单独读懂和测试的功能。当我们说不时，会解释为什么——fork 永远是被允许的（这正是 MIT 的意义）。

## 关于安全

因为 Waku 跑在用户自己的机器上、用自己的密钥，PR 绝不能加入隐藏的网络调用、读取或外传密钥/`.env`、或在安装时执行代码。保持本地、保持清晰。

## 社区

问题、演示分享、结对调试：[Discord](https://discord.gg/7Ntxzm3eJ)。贡献即表示你同意你的工作在仓库的 MIT license 下授权。
