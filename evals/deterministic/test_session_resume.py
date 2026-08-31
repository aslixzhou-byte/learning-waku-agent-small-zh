"""确定性评估——dashboard 会恢复其最近的会话。

Sean 遇到的线上 bug：每次服务器重启（开发中经常发生），聊天就“消失”了——
_dash_session 在每次进程都会新建一个带日期的会话，于是面板加载为空，而真实
对话停在了上一个带时间戳的 id 下。修复：启动时，如果最近一个 dashboard 会话
的最后一条消息仍在空闲窗口内，就恢复它；否则全新开始。"""

from __future__ import annotations

from evals.helpers import ScriptedClient, make_waku
from waku.ops.dashboard import _resume_or_new_session


def _seed(app, session_id, age_minutes, source="dashboard"):
    app.conn.execute(
        "INSERT INTO chat_log (role, content, session_id, created_at, source) "
        "VALUES ('user', 'hi', ?, datetime('now', ?), ?)",
        (session_id, f"-{age_minutes} minutes", source),
    )
    app.conn.commit()


def test_recent_dashboard_thread_is_resumed(tmp_path, monkeypatch):
    monkeypatch.setenv("WAKU_SESSION_IDLE_MINUTES", "60")
    app = make_waku(tmp_path / "home", client=ScriptedClient([]))
    _seed(app, "dashboard-20260101-120000", age_minutes=5)     # 新鲜的
    assert _resume_or_new_session(app.conn) == "dashboard-20260101-120000"


def test_idle_thread_is_not_resumed(tmp_path, monkeypatch):
    monkeypatch.setenv("WAKU_SESSION_IDLE_MINUTES", "60")
    app = make_waku(tmp_path / "home", client=ScriptedClient([]))
    _seed(app, "dashboard-20260101-120000", age_minutes=120)   # 空闲 2h > 60m
    got = _resume_or_new_session(app.conn)
    assert got != "dashboard-20260101-120000"
    assert got.startswith("dashboard-")


def test_most_recent_of_several_threads_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("WAKU_SESSION_IDLE_MINUTES", "60")
    app = make_waku(tmp_path / "home", client=ScriptedClient([]))
    _seed(app, "dashboard-20260101-090000", age_minutes=40)
    _seed(app, "dashboard-20260101-100000", age_minutes=10)    # 更新的
    assert _resume_or_new_session(app.conn) == "dashboard-20260101-100000"


def test_only_dashboard_source_threads_are_resumed(tmp_path, monkeypatch):
    """最近的 telegram/cli 会话绝不能劫持 dashboard 的恢复——
    按 source 而非 id 匹配。"""
    monkeypatch.setenv("WAKU_SESSION_IDLE_MINUTES", "60")
    app = make_waku(tmp_path / "home", client=ScriptedClient([]))
    _seed(app, "telegram-12345", age_minutes=1, source="telegram")
    got = _resume_or_new_session(app.conn)
    assert got.startswith("dashboard-") and got != "telegram-12345"


def test_new_chat_s_prefixed_thread_is_resumed(tmp_path, monkeypatch):
    """回归测试：'+ 新建聊天' 会创建 's-...' 的 id。按 source 恢复（而非
    'dashboard-%' 的 id 过滤）意味着这些会话也能在重启后存活——这正是 Sean
    遇到的、服务器重启后新聊天“消失”的 bug。"""
    monkeypatch.setenv("WAKU_SESSION_IDLE_MINUTES", "60")
    app = make_waku(tmp_path / "home", client=ScriptedClient([]))
    _seed(app, "s-20260101-120000", age_minutes=3, source="dashboard")
    assert _resume_or_new_session(app.conn) == "s-20260101-120000"
