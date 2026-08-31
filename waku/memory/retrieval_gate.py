"""高光时刻 #1 —— 决定是否要检索记忆的门禁。

各平台最常被问的问题："为什么每一轮都要访问记忆库？"默认开启检索 (a) 慢 ——
每次回复前都要多一次搜索 —— 而且 (b) 更糟：无关记忆会干扰答案（"过度解读"）。

所以在触碰任何存储之前，一个便宜快速的小模型先回答一个问题：
    这条消息需要用到用户的记忆吗？
"2+2 等于几" → 不需要。"我什么时候见 Alex？" → 需要，并给出检索关键词。

成本：一次小模型调用（约几百 token）。收益：只在有用时才检索。
这与 evals 里 LLM-as-judge 的裁判模式相同 —— 一个小模型做出一个单一的窄决策。
"""

from __future__ import annotations  # 让类型注解（如 tuple[bool, str, str]）在旧版 Python 里也能用

import json  # 解析模型返回的 JSON 决策

import anthropic  # Anthropic 客户端：发起门禁调用

# 门禁提示词：让模型判断这条用户消息是否需要检索用户的长期记忆，并输出纯 JSON 的检索决策
GATE_PROMPT = """\
You are a retrieval gate for a personal assistant's long-term memory.
Given the user's message, decide if answering well requires the user's stored
memories (facts about people, projects, preferences, or past events).

Reply with ONLY this JSON, nothing else:
{{"retrieve": true/false, "query": "<search keywords if true, else empty>", "reason": "<5 words>"}}

General knowledge, math, small talk, or self-contained requests → false.
Anything referencing the user's life, people, plans, or history → true.

User message: {message}"""


def should_retrieve(
    client: anthropic.Anthropic,  # Anthropic 客户端：发起门禁决策调用
    small_model: str,  # 便宜快速的门禁小模型名
    message: str,  # 用户这条消息，交给门禁判断
) -> tuple[bool, str, str]:
    """返回 (是否检索?, 检索关键词, 原因)。失败时开放：如果门禁自身出错，
    我们仍然检索 —— 一条过时的记忆胜过一条丢失的记忆。"""
    try:
        response = client.messages.create(  # 发起一次门禁决策调用
            model=small_model,
            # 宽裕的预算：推理模型（Kimi K3 等）会在 JSON 之前先花一个思考块 ——
            # 之前 100 token 会把答案截断掉
            max_tokens=600,
            messages=[{"role": "user", "content": GATE_PROMPT.format(message=message)}],
        )
        text = "".join(b.text for b in response.content if b.type == "text")  # 提取纯文本回复
        if "{" not in text:   # 只有推理/被截断的回复，不是错误
            return True, message, "gate returned no JSON — failing open"  # 拿不到 JSON 就开放失败：照样检索
        decision = json.loads(text[text.index("{") : text.rindex("}") + 1])  # 截出首尾大括号之间的 JSON 再解析
        # query/reason 缺省时回退到原消息/空串，保证返回总能取到值
        return bool(decision.get("retrieve")), decision.get("query", message), decision.get("reason", "")
    except Exception as exc:  # 门禁自身抛异常（网络/超时等）同样走开放失败
        return True, message, f"gate failed open ({type(exc).__name__})"  # 宁可多检索，不可漏掉记忆
