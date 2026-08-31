---
name: weekly-brief
description: Brief me on my week, my day, or what to focus on. Use for "brief me", "what's on my week", "what should I focus on", "my day", "catch me up", morning briefing.
---

<!-- 中文说明（供开发者阅读，本文件是发给 LLM 的提示词，正文保持英文）：
  description 含义：给我简报我的一周、我的一天或该关注什么。用于“简报我”“我这周有什么”“我该关注什么”“我的一天”“帮我跟上”“晨间简报”等场景。
  正文逐段中文对照如下。
-->

## How to brief the user
<!-- 中文：如何给用户做简报 -->

1. Call `read_apple_calendar` (7 days) to get their real schedule — including
   events that arrived by email invite.
   <!-- 中文：调用 read_apple_calendar（7 天）拿真实日程——包括通过邮件邀请进来的事件。 -->
2. Call `read_apple_mail` (48 hours) to see what's landed in their inbox. Keep
   the `message://` link for anything worth surfacing.
   <!-- 中文：调用 read_apple_mail（48 小时）看收件箱进了什么。值得提起的都保留 message:// 链接。 -->
3. Check memory (the retrieval gate handles this) for their people, projects,
   and preferences so the brief is personal.
   <!-- 中文：查记忆（检索门禁会处理）里的人、项目和偏好，让简报更个性化。 -->

Then write a **focus-first** briefing, not a data dump:
<!-- 中文：然后写一份“聚焦优先”的简报，而不是数据堆砌： -->

- Open with the 1-3 things that actually matter this week (deadlines, key
  meetings, anything time-sensitive from mail).
  <!-- 中文：开篇先讲本周真正要紧的 1-3 件事（截止日期、关键会议、邮件里有时效性的内容）。 -->
- Group the rest by day. Note who each meeting is with and why it matters if
  you know them from memory.
  <!-- 中文：其余按天分组。若记忆里认识，注明每场会议是和谁、为什么重要。 -->
- For emails that need action, give a one-line "why" and paste the
  `message://…` link so they can jump straight to it in Mail.
  <!-- 中文：对需要处理的邮件，给一行“为什么”，并贴 message://… 链接，方便直接在 Mail 里跳转。 -->
- End with a short "suggested focus for today."
  <!-- 中文：结尾给一句简短的“今日建议关注”。 -->

Keep it skimmable — short lines, no filler. If Calendar or Mail is unavailable
(permission not granted), say so plainly and brief on what you can.
<!-- 中文：保持可快速浏览——短句、无废话。如果日历或邮件不可用（未授权），如实说明，并就你能做的部分简报。 -->
