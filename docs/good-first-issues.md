# 适合新手的问题（发布后在 GitHub 上登记）

每条都刻意做成第一份 PR 的样子：一个文件、一个可以照着学的清晰参考实现、以及一种验证方式。

1. **Discord 网关**——`waku/gateway/discord.py`，照 Telegram 那个来（discord.py，消息进 → `waku.respond()` → 消息出）。参考：`waku/gateway/telegram.py`。验证：跟你的 bot 聊天。

2. **WhatsApp 网关（Meta Cloud API）**——同样的形状，基于 webhook；在模块 docstring 里如实记录 Meta 配置的折腾。参考：`waku/gateway/telegram.py`。

3. **Google Calendar 适配器**——在同一个工具 schema 后面替换 `waku/tools/calendar.py` 的内部实现（用环境变量门控，mock 仍是默认）。验证：确定性评测在 mock 下依然通过。

4. **Notion 情景适配器**——`waku/memory/episodic/notion_store.py`，同样的 `add`/`search`/`recent` 接口。参考：`SqliteEpisodeStore`。

5. **`/memory` CLI 命令**——在 REPL 里展示 Waku 知道什么：事实、情节、以及未整合的聊天数。纯 SQLite 读取；演示利器。

6. **Trace 美化打印**——`python -m waku.ops.show_trace [file]` 在终端里把 JSONL 追踪渲染成缩进时间线（rich）。不需要 OTel。

7. **社区 skills**——不是 issue，而是一份长期邀请：`skills/TEMPLATE.md`。
