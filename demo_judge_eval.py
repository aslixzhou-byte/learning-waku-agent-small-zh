"""judge 测评 demo LLM 当裁判，演示「质量打分」怎么做、怎么看。

流程：
  1. 跑一轮真实 Waku，拿到模型回复（这就是「被评测的对象」）
  2. 用一个裁判 prompt，让 LLM 按评分标准给这个回复打 0-10 分
  3. 阈值 6，>= 6 算通过

和 deterministic（0/1 断言工具触发没触发）的区别：
  这里测的是「回复好不好」这种开放式问题，没有唯一正确答案，所以让 LLM 当裁判。

跑法：
    python demo_judge_eval.py
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from waku.app import Waku
from waku.config import Settings, load_settings
from waku.loop.models import get_client


THRESHOLD = 6  # 评分范围 0-10，>= 6 算通过


"""
{
    "id": "schedule-basic", 
    "input": "Schedule a coffee with Alex next Tuesday at 9am",
    "expect_tool": "create_event",
    "expect_in_args": {
        "title": "alex",
        "start": "T09:00"
        }
}
"""

def judge_reply(task: str, reply: str, criteria: str) -> dict:
    """让 LLM 当裁判，给回复打分，返回 {"score": int, "reason": str}。"""
    settings = load_settings()
    client = get_client(settings) # 先建 client，顺便填充默认模型 id
    model = settings.small_model or settings.model

    # 裁判 prompt（发给 LLM 的原文，保留英文）
    prompt = (
        "You are a strict judge. Score the assistant reply 0-10.\n"
        f"Criteria: {criteria}\n\n"
        f"User: {task}\n"
        f"Reply: {reply}\n\n"
        'Reply with ONLY JSON: {"score": <int>, "reason": "<one short sentence>"}'
    )

    print(f"  裁判模型: {settings.provider}:{model}")
    print(f"  评分标准(criteria): {criteria}")
    print(f"  裁判看到的用户问题: {task}")
    print(f"  裁判看到的回复: {reply}\n")
    print("  >>> 裁判打分中...\n")

    resp = client.messages.create(
        model=model,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    verdict = json.loads(text[text.index("{") : text.rindex("}") + 1])
    return verdict

def run_one(task: str, criteria: str, setup_fact=None) -> bool:
    """跑一个评测：Waku 回复 → 裁判打分 → 判定过/不过。"""
    print("=" * 72)
    if setup_fact:
        print(f"预置记忆: {setup_fact[0]} → {setup_fact[1]}")
    print(f"用户输入: {task}\n")

    # ---- 第 1 步：跑一轮真实 Waku，拿到模型回复
    home = Path(tempfile.mkdtemp(prefix="demo-judge-"))
    app = Waku(settings=Settings(home=home, apple_calendar=False))
    app.session.session_id = "demo"
    if setup_fact:
        app.memory.facts.add(setup_fact[0], setup_fact[1])

    print(">>> Waku 跑起来了...\n")
    result = app.respond(task, source="demo")
    reply = result.reply
    print(f"Waku 回复: {reply}\n")

    # ---- 第 2 步：裁判按 criteria 打分
    verdict = judge_reply(task, reply, criteria)
    score = int(verdict["score"])
    reason = verdict.get("reason", "")
    passed = score >= THRESHOLD

    print("-" * 72)
    print(f"  得分: {score}/10  （阈值 {THRESHOLD}）")
    print(f"  理由: {reason}")
    print(f"  [{'通过' if passed else '失败'}]")
    print("=" * 72 + "\n")
    return passed


def main() -> None:
    print("judge 测评 demo 两个 case：\n")

    # case 1：约咖啡的回复是否 helpful（直接回应、确认动作、简洁亲切）
    run_one(
        task="Schedule a coffee with Alex next Tuesday at 9am",
        criteria="directly addresses the request, confirms the action (what/when/who), concise and warm",
    )

    # case 2：是否用上「Alex 喜欢早会」这个记忆偏好
    run_one(
        task="Book a catch-up with Alex on Friday",
        criteria="uses the remembered fact that Alex prefers morning meetings",
        setup_fact=("alex", "Alex prefers morning meetings"),
    )


if __name__ == "__main__":
    main()
