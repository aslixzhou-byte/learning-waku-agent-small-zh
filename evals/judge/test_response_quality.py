"""LLM 作为裁判——运行 Waku，然后让模型判断回复是否足够好。

这不是单元测试：得分是软性的（0–10 + 阈值）。隔壁的确定性评估负责 0/1 的
工具/状态检查。需要激活提供方的 API 密钥。

用 `pytest -s` 运行即可看到 [judge] 输入 / 回复 / 得分日志。
"""

from __future__ import annotations

import json

import pytest

from evals.helpers import HAS_KEY, make_waku
from waku.config import load_settings
from waku.loop.models import get_client

pytestmark = pytest.mark.skipif(not HAS_KEY, reason="LLM-as-judge needs an API key")

THRESHOLD = 6  # 评分范围 0–10


def _judge(task: str, reply: str, criteria: str) -> dict:
    """一次提示词 → {"score": int, "reason": str}。"""
    settings = load_settings()
    client = get_client(settings)
    model = settings.small_model or settings.model
    # 发送给 LLM 的裁判提示词：要求作为严格裁判，按 0-10 给助手回复打分，
    # 并只回复 JSON。
    prompt = (
        f"You are a strict judge. Score the assistant reply 0-10.\n"
        f"Criteria: {criteria}\n\n"
        f"User: {task}\n"
        f"Reply: {reply}\n\n"
        'Reply with ONLY JSON: {"score": <int>, "reason": "<one short sentence>"}'
    )
    print("\n" + "=" * 60, flush=True)
    print(f"[judge] provider={settings.provider}  model={model}", flush=True)
    print(f"[judge] criteria: {criteria}", flush=True)
    print(f"[judge] input:  {task}", flush=True)
    print(f"[judge] reply:  {reply}", flush=True)
    print(f"[judge] calling judge...", flush=True)

    resp = client.messages.create(
        model=model,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    print(f"[judge] raw: {text.strip()}", flush=True)
    verdict = json.loads(text[text.index("{") : text.rindex("}") + 1])
    score = int(verdict["score"])
    print(
        f"[judge] VERDICT  score={score}  threshold={THRESHOLD}  "
        f"pass={score >= THRESHOLD}  reason={verdict.get('reason', '')}",
        flush=True,
    )
    print("=" * 60, flush=True)
    return verdict


def test_scheduling_reply_is_helpful(tmp_path):
    app = make_waku(tmp_path / "home")
    msg = "Schedule a coffee with Alex next Tuesday at 9am"
    print(f"\n[judge] Waku.respond: {msg!r}", flush=True)
    result = app.respond(msg)

    verdict = _judge(
        msg,
        result.reply,
        # 裁判提示词的评分标准：直接回应请求、确认动作（做什么/何时/谁）、简洁而亲切
        "directly addresses the request, confirms the action (what/when/who), concise and warm",
    )
    assert int(verdict["score"]) >= THRESHOLD, verdict


def test_reply_uses_remembered_preference(tmp_path):
    app = make_waku(tmp_path / "home")
    app.memory.facts.add("alex", "Alex prefers morning meetings")
    msg = "Book a catch-up with Alex on Friday"
    print(f"\n[judge] seeded fact + Waku.respond: {msg!r}", flush=True)
    result = app.respond(msg)

    verdict = _judge(
        msg,
        result.reply,
        # 裁判提示词的评分标准：用上了记住的事实——Alex 更喜欢上午开会
        "uses the remembered fact that Alex prefers morning meetings",
    )
    assert int(verdict["score"]) >= THRESHOLD, verdict
