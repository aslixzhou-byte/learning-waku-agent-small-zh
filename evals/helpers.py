"""共享的评估基础设施：为离线测试提供脚本化的假 LLM 客户端，为真实评估提供
真实 Waku 的工厂函数。"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace


def has_key() -> bool:
    """当 ACTIVE 提供方（WAKU_PROVIDER）已配置密钥时返回 True，这样真实评估会
    运行在用户实际配置的提供方上（anthropic、openrouter、gemini 等），而不只
    检查 ANTHROPIC_API_KEY。

    在调用时计算——之前的测试可能通过 dashboard.apply_settings 修改
    os.environ，因此导入时的快照会过期。
    """
    from waku.config import load_settings
    from waku.loop.models import PROVIDERS

    settings = load_settings()
    provider = PROVIDERS.get(settings.provider)
    return bool(
        (settings.api_key or "").strip()
        or (provider and (os.getenv(provider.key_env) or "").strip())
    )


# 供收集阶段 skipif 使用的向后兼容别名。真实测试在运行时也会调用 has_key()，
# 因为 apply_settings 可能在套件运行中途污染 os.environ。
HAS_KEY = has_key()


def text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def tool_block(name: str, args: dict, call_id: str = "tu_1"):
    return SimpleNamespace(type="tool_use", id=call_id, name=name, input=args)


def response(blocks, stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=0, output_tokens=0),
        content=blocks,
    )


class ScriptedClient:
    """按固定列表回放响应——充当离线测试中的“模型”。"""

    def __init__(self, script: list):
        self._script = list(script)
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        return self._script.pop(0)


def make_waku(home: Path, client=None, **settings_overrides):
    """用隔离的 home 目录构建一个 Waku；可选用假客户端替换真实模型。"""
    from waku.app import Waku
    from waku.config import Settings

    # 无论 .env 如何配置，评估时绝不能触碰真实的 Apple Calendar
    settings_overrides.setdefault("apple_calendar", False)
    settings = Settings(home=home, **settings_overrides)
    if client is not None and not settings.api_key:
        settings.api_key = "offline"  # 脚本化运行时不读取真实密钥
    return Waku(settings=settings, client=client)
