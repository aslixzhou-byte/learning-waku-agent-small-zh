"""确定性评估——工作记忆的组装是纯字符串逻辑。

针对在 dashboard 上发现的一个线上 bug 的回归保护网：代理有日期但没有时间，
于是它在安排“30 分钟后”的事情前先问用户“现在几点了？”。系统提示词必须
携带真实的时钟。
"""

from __future__ import annotations

import re

from waku.config import load_settings
from waku.runtime.session import Session


def test_system_prompt_includes_current_time():
    settings = load_settings()
    settings.ensure_home()
    system = Session(settings, memory=None).build_system("what should I do in 30 minutes?")
    # 必须存在 HH:MM 时钟——而不只是日期——这样模型永远不必向用户询问时间
    # （这是线上 bug）。
    assert re.search(r"\b\d{2}:\d{2}\b", system), "system prompt is missing a HH:MM time"
    assert "Right now it is" in system


def test_session_tags_history_with_its_session_id():
    # 会话只是一个 session_id 标签；新的 Session 携带默认值。
    settings = load_settings()
    assert Session(settings, memory=None).session_id == "default"
    s = Session(settings, memory=None)
    s.start_new("s-test")
    assert s.session_id == "s-test" and s.history == []


def test_system_prompt_includes_own_model_identity():
    """dashboard 上的线上 bug（K3 发布当天）：被问到“你是什么模型”时，代理说
    它不知道自己运行在什么上。系统提示词必须写出模型 + 提供方，这样代理才能
    如实回答。"""
    settings = load_settings()
    settings.ensure_home()
    settings.provider, settings.model = "kimi", "kimi-k3"
    system = Session(settings, memory=None).build_system("what model are you?")
    assert "kimi-k3" in system and "'kimi' provider" in system
