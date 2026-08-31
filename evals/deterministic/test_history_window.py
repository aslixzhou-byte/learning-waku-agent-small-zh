"""确定性评估——工作记忆是一个有界的滑动窗口。

Sean 在测试 Telegram 时的发现：它那一个常驻会话会无限累积历史，每一轮都会把
全部内容重新发送（无界上下文 -> 成本/延迟攀升 -> 最终触及上下文上限而崩溃）。
工作记忆必须是一个固定窗口；更早的轮次存放在 state.db + 记忆整合中，而非
提示词里。

注意：chat_log 保留*所有*轮次（记忆整合会读取它们）。窗口仅在从
session.history 组装提示词时生效。"""

from __future__ import annotations

from evals.helpers import ScriptedClient, make_waku, response, text_block


def _gate_skip():
    return response([text_block('{"retrieve": false, "query": "", "reason": "t"}')])


def test_prompt_history_is_windowed(tmp_path, monkeypatch):
    monkeypatch.setenv("WAKU_HISTORY_TURNS", "3")
    script = []
    for _ in range(5):
        script += [_gate_skip(), response([text_block("ok")])]
    app = make_waku(tmp_path / "home", client=ScriptedClient(script))
    for i in range(5):
        app.respond(f"message number {i}")

    # respond() 喂给模型的同一段切片：最后 N 轮（每轮 2 行）
    window = app.settings.history_turns * 2
    prompt_msgs = app.session.history[-window:]
    blob = " ".join(m["content"] for m in prompt_msgs)

    assert len(prompt_msgs) <= window
    assert "message number 0" not in blob   # 最旧的轮次已从提示词中剔除
    assert "message number 4" in blob       # 最新的轮次仍在其中

    # chat_log 仍保留完整会话（5 轮 × 用户+助手）
    n = app.conn.execute("SELECT COUNT(*) FROM chat_log").fetchone()[0]
    assert n == 10


def test_default_window_is_generous_but_finite(tmp_path, monkeypatch):
    monkeypatch.delenv("WAKU_HISTORY_TURNS", raising=False)
    app = make_waku(tmp_path / "home", client=ScriptedClient([]))
    assert app.settings.history_turns == 12
