---
name: your-skill-name
description: One sentence saying what this skill does AND when to use it — the loader matches user messages against these words, so include the words people actually say.
---

<!--
贡献方式：把本文件复制到 skills/community/<your-skill-name>/SKILL.md 并开 PR。
CI 会检查 frontmatter（name + description 必填——官方 Anthropic Agent Skills 格式）。
正文控制在约 60 行以内：skill 只有在命中时才会被加载进 prompt，但越短越好。
-->

## 使用说明

给模型的逐步指引。要具体：点名要调用的工具（`create_event`、`save_note`、`send_message`）、要假设的默认值、以及要采取的语气。

## 边界情况

| 场景 | 怎么做 |
|---|---|
| 有歧义时 | 问一个澄清问题 |
