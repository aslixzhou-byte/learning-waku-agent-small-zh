---
name: schedule-meeting
description: Schedule meetings, calls, or events on the calendar. Use when the user wants to book, plan, schedule, or set up a meeting or appointment with someone at a time.
---

<!-- 中文说明（供开发者阅读，本文件是发给 LLM 的提示词，正文保持英文）：
  description 含义：在日历上安排会议、通话或事件。当用户想在某时间与某人预订、规划、安排或约见时使用。
  正文逐段中文对照如下。
-->

## How to schedule well
<!-- 中文：如何把日程安排做好 -->

1. Resolve relative dates ("next Tuesday", "tomorrow morning") into ISO 8601
   using today's date from the system prompt. Morning = 09:00, afternoon =
   14:00, evening = 18:00 unless the user says otherwise.
   <!-- 中文：用系统提示词里的今天日期，把相对日期（“下周二”“明早”）换算成 ISO 8601。
        除非用户另有说明，上午=09:00、下午=14:00、晚上=18:00。 -->
2. Check memory context for the attendee's preferences (e.g. "prefers morning
   meetings") and apply them — mention it when you do ("since Alex prefers
   mornings, I booked 9am").
   <!-- 中文：查看记忆上下文里与会者的偏好（如“偏好上午开会”）并应用——应用时说明一句
        （“因为 Alex 偏好上午，我约了 9 点”）。 -->
3. Call `create_event` with a short, specific title: "Coffee with Alex", not
   "Meeting".
   <!-- 中文：调用 create_event，标题要短而具体：“和 Alex 喝咖啡”，而不是“会议”。 -->
4. If the user mentioned an agenda or context, put it in `notes`.
   <!-- 中文：如果用户提到了议程或背景，写进 notes。 -->
5. After creating, confirm in one sentence: what, when, with whom.
   <!-- 中文：创建后用一句话确认：做什么、什么时候、和谁。 -->

## Edge cases
<!-- 中文：边界情况 -->

| Situation | Do |
|---|---|
| No time given | Propose a concrete time instead of asking an open question — if memory shows the attendee's preference, lead with it ("Alex prefers mornings — Friday 9am?"). Only ask openly when memory gives you nothing |
| Past date requested | Point it out, suggest the next occurrence |
| Attendee unknown to memory | Schedule anyway; offer to `save_note` who they are |

<!-- 中文对照：
  | 没有给时间 | 主动提一个具体时间而不是开放式反问——若记忆里有与会者偏好就先按它来（“Alex 偏好上午——周五 9 点？”）。只有记忆里没有时才开放式询问 |
  | 要的是过去的日期 | 指出来，建议下一次 |
  | 与会者不在记忆里 | 照常安排；顺带提议用 save_note 记下他是谁 |
-->
