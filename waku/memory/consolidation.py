"""记忆整合 把对话蒸馏成持久记忆，但只在必要时进行。

只有新增了 N 次聊天后才整合
每条消息都跑一次摘要器既浪费又嘈杂；
把 N 次交互批量处理，才能让摘要器有足够的上下文去提取值得保留的事实。

一个便宜的小模型读取未整合的聊天日志，产出：
  - facts   → 语义记忆（"Alex 喜欢早上的会议"）
  - episode → 情景记忆（"2026-07-10：和 Alex 策划了 Acme 演示"）
"""

from __future__ import annotations  # 让类型注解（如 anthropic.Anthropic）在旧版 Python 里也能用

import json  # 解析模型返回的 JSON 蒸馏结果
from datetime import date  # 生成今天的 ISO 日期，作为 episode 的时间戳

import anthropic  # Anthropic 客户端：发起蒸馏调用

from waku.memory.episodic.store import SqliteEpisodeStore  # 情景记忆存储（写摘要事件）
from waku.memory.semantic.store import SqliteFactStore  # 语义记忆存储（写事实）

# 摘要提示词：让模型把助手近期的对话蒸馏成长期记忆，输出纯 JSON 的事实列表和一句情景摘要
SUMMARIZER_PROMPT = """\
You distill a personal assistant's recent conversation into long-term memory.

From the exchanges below, extract:
1. durable facts about the user, their people, projects, or preferences —
   only things worth remembering in a month; skip chit-chat and one-offs.
2. one single-sentence episode summarizing what happened in this conversation.

Reply with ONLY this JSON:
{{"facts": [{{"subject": "<who/what>", "content": "<one sentence>"}}], "episode": "<one sentence>"}}

Exchanges:
{log}"""


def consolidate_if_due(
    conn,  # SQLite 连接：读取未整合日志、更新 consolidated 标记
    client: anthropic.Anthropic,  # Anthropic 客户端：发起蒸馏调用
    small_model: str,  # 用于蒸馏的便宜小模型名
    every_n: int,  # 每「N 次交互」整合一次（2 行日志算 1 次交互）
    facts: SqliteFactStore,  # 语义记忆存储：写入提取出的事实
    episodes: SqliteEpisodeStore,  # 情景记忆存储：写入一句情景摘要
) -> int:
    """返回写入了多少条新事实（0 = 尚未到期，或没有值得保留的内容）。"""

    print("满N轮交互做一次记忆整合: consolidation.consolidate_if_due")

    rows = conn.execute(  # 找出所有尚未整合过的日志行
        "SELECT id, role, content FROM chat_log WHERE consolidated = 0 ORDER BY id"
    ).fetchall()
    if len(rows) < every_n * 2:  # 每次交互 = 2 行（user + assistant）
        return 0  # 还没攒够 N 次交互，这轮什么也不做（即"只有在新增了 N 次聊天后才整合"）

    log = "\n".join(f"{r['role']}: {r['content']}" for r in rows)  # 拼成 "user: ...\nassistant: ..." 的对话稿喂给模型
    try:
        response = client.messages.create(  # 用便宜的小模型跑一次批量蒸馏
            model=small_model,
            max_tokens=600,
            messages=[{"role": "user", "content": SUMMARIZER_PROMPT.format(log=log)}],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        distilled = json.loads(text[text.index("{") : text.rindex("}") + 1])  # 截出首尾大括号之间的 JSON 再解析
    except Exception:
        return 0  # 绝不丢失日志 —— 它保持未整合状态，留待下次处理

    print("try 小模型 整理对话信息")

    for fact in distilled.get("facts", []):
        if fact.get("subject") and fact.get("content"):  # 跳过缺字段的脏事实
            print("添加事实记忆 facts.add: ")
            facts.add(fact["subject"], fact["content"], source="consolidation")  # 来源标记为整合，便于追溯
    if distilled.get("episode"):  # 模型给了一句话摘要才写情景记忆
        print("添加情景记忆 facts.add: ")
        episodes.add(distilled["episode"], happened_at=date.today().isoformat())  # 时间戳用今天

    conn.execute(  # 把这批日志标记为已整合，避免下次重复处理
        f"UPDATE chat_log SET consolidated = 1 WHERE id IN ({','.join('?' * len(rows))})",
        [r["id"] for r in rows],
    )
    conn.commit()
    return len(distilled.get("facts", []))  # 返回本次写入的事实条数
