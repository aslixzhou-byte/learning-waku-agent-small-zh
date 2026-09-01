"""工具注册表
一个工具由三部分组成：模型读取的名称+描述、参数用的 JSON schema、
以及一个会被执行的 Python 函数。
（注册表模式改编自 launch-agentic-rag 的 app/agents/tools/registry.py。）
"""

from __future__ import annotations  # 让类型注解（如 dict[str, Any]）在旧版 Python 里也能用

from dataclasses import dataclass  # dataclass：自动生成 __init__/__repr__ 等样板方法
from typing import Any, Callable   # Any=任意类型；Callable=可调用对象（函数）类型


@dataclass  # 标记为数据类：自动生成 __init__，省去手写样板
class Tool:
    """一个「工具」的完整定义：模型能读到什么 + 代码会执行什么。"""
    name: str                       # 工具名（模型据此调用，如 "create_event"）
    description: str                # 工具描述（发给 LLM，告诉它何时该用这个工具）
    input_schema: dict[str, Any]    # 参数的 JSON schema（告诉模型要传哪些参数、各自类型）
    fn: Callable[..., str]          # 真正执行工具逻辑的函数；返回一个字符串供模型观察

    def to_api(self) -> dict[str, Any]:
        """转成 Messages API 在 `tools=` 参数里期望的字典形状。"""
        return {
            "name": self.name,                # 工具名
            "description": self.description,  # 工具描述
            "input_schema": self.input_schema,  # 参数 schema
        }


class ToolRegistry:
    """工具注册表：持有所有已注册的工具，并提供查询与「安全执行」。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}   # 名字 → 工具对象 的字典（下划线开头 = 内部私有）

    def register(self, tool: Tool) -> None:
        """注册一个工具：以工具名为键存进字典。"""
        self._tools[tool.name] = tool       # 同名工具会被后者覆盖（预期每个名字唯一）

    def schemas(self) -> list[dict[str, Any]]:
        """返回所有工具的 API schema 列表 —— 这就是发给 LLM 的 tools= 参数。"""
        return [t.to_api() for t in self._tools.values()]  # 遍历所有工具，逐个转成 API 形状

    def execute(self, name: str, args: dict[str, Any]) -> str:
        """安全地执行一次工具调用：模型把错误当作文本观察，
        而不是让循环崩溃（execute_tool_safely 模式）。"""
        tool = self._tools.get(name)    # 按名字取工具；取不到时返回 None（不抛异常）
        if tool is None:                # 模型可能幻觉出一个根本不存在的工具名
            return f"Error: unknown tool '{name}'"  # 返回错误文本，让模型看到并自我纠正
        try:
            return tool.fn(**args)      # 真正调用工具函数，把参数字典解包成关键字参数传入
        except Exception as exc:        # 工具内部报错时，不能让它把整个循环带崩
            return f"Error running {name}: {exc}"  # 把异常转成文本返回，模型可据此调整重试
