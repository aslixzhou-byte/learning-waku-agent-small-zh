"""确定性评估——聊天面板的“所有消息”时间线。

Sean 的反馈：Loop 标签页会展示所有会话的每一轮，但聊天面板一次只显示一个
会话，所以他感觉历史记录缺失了。只读的 id="__all__" 历史操作返回完整的
跨会话时间线（最新的在最后），让聊天面板能像 Loop 那样渲染完整对话。"""

from __future__ import annotations

import json

from evals.helpers import ScriptedClient, make_waku
from waku.ops.dashboard import _thread_history, session_action


def _seed(app, session_id, user, assistant):
    for role, content in (("user", user), ("assistant", assistant)):
        app.conn.execute(
            "INSERT INTO chat_log (role, content, session_id, source) VALUES (?, ?, ?, 'dashboard')",
            (role, content, session_id),
        )
    app.conn.commit()


def test_all_history_returns_every_thread(tmp_path, monkeypatch):
    monkeypatch.setenv("WAKU_HOME", str(tmp_path / "home"))
    app = make_waku(tmp_path / "home", client=ScriptedClient([]))
    _seed(app, "dashboard-a", "hi from A", "reply A")
    _seed(app, "dashboard-b", "hi from B", "reply B")

    out = session_action({"action": "history", "id": "__all__"})
    contents = [m["content"] for m in out["history"]]
    # 每个会话都存在，按从旧到新排序
    assert contents == ["hi from A", "reply A", "hi from B", "reply B"]


def test_single_thread_history_is_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("WAKU_HOME", str(tmp_path / "home"))
    app = make_waku(tmp_path / "home", client=ScriptedClient([]))
    _seed(app, "dashboard-a", "hi from A", "reply A")
    _seed(app, "dashboard-b", "hi from B", "reply B")

    out = session_action({"action": "history", "id": "dashboard-b"})
    assert [m["content"] for m in out["history"]] == ["hi from B", "reply B"]


def test_thread_history_includes_meta(tmp_path, monkeypatch):
    """回归测试：切换会话时只显示文本，因为该路径丢弃了 meta。现在切换和历史
    两条路径都走 _thread_history，它必须携带每轮的 meta（gate/stats/tools/model）
    以便卡片完整渲染。"""
    monkeypatch.setenv("WAKU_HOME", str(tmp_path / "home"))
    app = make_waku(tmp_path / "home", client=ScriptedClient([]))
    meta = {"gate": {"decision": "skip"}, "iterations": 1, "latency_ms": 2400,
            "tools": [], "model": "gemini-3.5-flash"}
    app.conn.execute("INSERT INTO chat_log (role, content, session_id, source) VALUES ('user','hi','t','dashboard')")
    app.conn.execute("INSERT INTO chat_log (role, content, session_id, source, meta) "
                     "VALUES ('assistant','hey','t','dashboard',?)", (json.dumps(meta),))
    app.conn.commit()

    hist = _thread_history(app.conn, "t")
    assert hist[0]["meta"] is None                     # 用户行
    assert hist[1]["meta"]["model"] == "gemini-3.5-flash"
    assert hist[1]["meta"]["gate"]["decision"] == "skip"
