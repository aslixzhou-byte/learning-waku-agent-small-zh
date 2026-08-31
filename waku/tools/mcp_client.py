"""MCP 连接器——把任何 Model Context Protocol 服务器接入 Waku 的工具。

Waku 的循环是同步的；MCP SDK 是异步的。下面的桥接器在一个守护线程上
运行一个 asyncio 事件循环，通过单个 AsyncExitStack 在该循环上持有每个
服务器的会话（anyio 要求栈必须在同一个任务中进入/退出），并让同步循环
通过 run_coroutine_threadsafe 调用工具。

配置：WAKU_HOME/mcp.json
  {"servers": [{"name": "fs", "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                "env": {}}]}

每个服务器的工具以 `<server>_<tool>` 注册到 ToolRegistry。连接失败的
服务器会被跳过并给出警告——Waku 仍然能启动。
"""

from __future__ import annotations  # 让类型注解（如 AsyncExitStack | None）在旧版 Python 里也能用

import asyncio    # 异步事件循环与协程调度
import json       # 解析 mcp.json 配置
import threading  # 在守护线程上跑事件循环
from contextlib import AsyncExitStack  # 统一进入/退出多个异步上下文（服务器会话）
from pathlib import Path  # 配置文件路径类型

from waku.tools.registry import Tool  # 工具定义类


class MCPBridge:
    """桥：在后台线程的 asyncio 循环里持有 MCP 会话，对外暴露同步的 Tool 调用入口。"""

    def __init__(self, config_path: Path, timeout: float = 30.0):
        self.config_path = config_path  # mcp.json 路径
        self.timeout = timeout  # 单次工具调用 / 连接的等待秒数
        self._loop = asyncio.new_event_loop()  # 专用事件循环（后台线程跑它）
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)  # 守护线程，不阻碍退出
        self._stack: AsyncExitStack | None = None  # 持有所有服务器会话；None 表示还没 start
        self._sessions: dict = {}  # 服务器名 → 已连接的 ClientSession

    def start(self) -> list[Tool]:
        """连接所有配置的服务器并返回它们的工具（作为 Tool）。"""
        self._thread.start()  # 启动后台线程，事件循环开始运行
        servers = json.loads(self.config_path.read_text()).get("servers", [])  # 读配置；没 servers 字段则为空表
        fut = asyncio.run_coroutine_threadsafe(self._connect_all(servers), self._loop)  # 把协程投递到后台循环
        listed = fut.result(self.timeout * 2)  # {server: [工具元数据]}；同步等待连接完成
        tools: list[Tool] = []
        for srv, metas in listed.items():  # 遍历每台服务器及其工具元数据
            for meta in metas:
                tools.append(Tool(
                    name=f"{srv}_{meta['name']}",  # 以 <服务器>_<工具> 命名，避免跨服务器撞名
                    # 提示词：以 [MCP:server] 前缀标记来源的工具描述
                    description=f"[MCP:{srv}] {meta.get('description','') or ''}",
                    input_schema=meta.get("inputSchema") or {"type": "object", "properties": {}},  # 缺 schema 则给空对象
                    fn=(lambda srv=srv, tname=meta["name"], **kw: self.call(srv, tname, kw)),  # 闭包：绑定服务器+工具名，转发调用
                ))
        return tools

    async def _connect_all(self, servers) -> dict:
        from mcp import ClientSession, StdioServerParameters  # 延迟导入：mcp 是可选依赖
        from mcp.client.stdio import stdio_client

        self._stack = AsyncExitStack()  # 所有会话挂在同一个栈上，close 时一并退出
        listed: dict = {}
        for spec in servers:  # 逐个服务器连接
            name = spec["name"]
            try:
                params = StdioServerParameters(  # 子进程参数：命令 + 参数 + 环境变量
                    command=spec["command"], args=spec.get("args", []), env=spec.get("env") or None
                )
                read, write = await self._stack.enter_async_context(stdio_client(params))  # 拉起服务器子进程的 stdio 管道
                session = await self._stack.enter_async_context(ClientSession(read, write))  # 建立客户端会话
                await session.initialize()  # 握手初始化
                self._sessions[name] = session  # 记录会话，供 call 使用
                tools = (await session.list_tools()).tools  # 拉取该服务器的工具清单
                listed[name] = [{"name": t.name, "description": t.description, "inputSchema": t.inputSchema} for t in tools]  # 转成纯数据
            except Exception as exc:  # 一个坏服务器不应拖垮其余的
                print(f"MCP server '{name}' failed to connect: {exc}")  # 只警告，继续连下一个
        return listed

    def call(self, server: str, tool: str, args: dict) -> str:
        """同步入口：把工具调用投递到后台循环，阻塞等待结果（带超时）。"""
        try:
            fut = asyncio.run_coroutine_threadsafe(self._acall(server, tool, args), self._loop)
            return fut.result(self.timeout)  # 同步等待，超时抛 TimeoutError
        except Exception as exc:  # 任何异常都转成文本返回，工具调用不崩溃循环
            return f"MCP call {server}_{tool} failed: {exc}"

    async def _acall(self, server: str, tool: str, args: dict) -> str:
        session = self._sessions.get(server)  # 按服务器名取会话
        if session is None:  # 没连上过：如实说明
            return f"MCP server '{server}' is not connected."
        result = await session.call_tool(tool, args)  # 调用远端工具
        parts = []
        for block in result.content:  # 遍历返回内容块
            parts.append(getattr(block, "text", None) or "[non-text content]")  # 文本块直接取；非文本给占位
        return "\n".join(parts) or "(no output)"  # 多块用换行拼起来；空则给占位

    def close(self) -> None:
        """优雅关闭：退出所有会话、停掉事件循环、等待线程结束。"""
        if self._stack is not None:  # 只在 start 过之后才需要关
            try:
                asyncio.run_coroutine_threadsafe(self._stack.aclose(), self._loop).result(10)  # 关闭所有子进程会话
            except Exception:  # 关闭失败也不能阻塞退出
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)  # 从循环自身线程安全地停止它
        self._thread.join(timeout=5)  # 等线程收尾（最多 5 秒）
