"""代理的工具集。旗舰任务工具（日历/笔记/消息）、记忆自管理
（manage_memory/update_soul/create_skill），以及可选适配器：
苹果生态（WAKU_APPLE_TOOLS=1）与 MCP 服务器（.waku/mcp.json）。"""

from __future__ import annotations  # 让类型注解（如 sqlite3.Connection）在旧版 Python 里也能用

import os        # 读取环境变量（WAKU_EXPERIMENTAL 等开关）
import sqlite3   # 提供数据库连接类型，传给各 make_* 工厂

from waku.config import Settings                              # 应用配置对象（home 目录、各开关）
from waku.tools import calendar, memory_admin, messages, notes, search  # 各工具模块（包内导入）
from waku.tools.registry import ToolRegistry                  # 工具注册表类


def build_registry(conn: sqlite3.Connection, settings: Settings, memory=None) -> ToolRegistry:
    """把各 make_* 工厂产出的 Tool 全部注册进同一个注册表，返回装配好的注册表。"""
    registry = ToolRegistry()  # 新建空注册表（内部是 name → Tool 的字典）
    registry.register(calendar.make_tool(conn, settings.home, apple_calendar=settings.apple_calendar))
    registry.register(calendar.make_list_tool(conn))   # 读侧："我的日历上有什么？"
    registry.register(notes.make_tool(conn))
    registry.register(messages.make_tool(settings.home))
    # 网页搜索 —— 与 create_event 配对，用于多工具循环演示
    #（"找出剩下的世界杯比赛并加到我的日历上"）。
    registry.register(search.make_tool())

    # 记忆自管理 —— 代理可以纠正/遗忘记忆、学习规则、
    # 并自己编写技能（感觉像个会学习的私人代理，而不是黑盒）。
    if memory is not None:  # 只有循环运行（携带 memory 对象）时才注册；CLI 直连调用则跳过
        registry.register(memory_admin.make_manage_memory_tool(memory))
        registry.register(memory_admin.make_update_soul_tool(settings))
        registry.register(memory_admin.make_create_skill_tool(settings, memory))

    # 实验性工具 —— 默认关闭；通过 WAKU_EXPERIMENTAL=1 启用。
    # delegate_task（通过 pi 的子代理）已可用；terminal/browser/cron 仍是
    # 骨架，会返回 "coming soon"。
    # 两处都可开启：settings.experimental 或环境变量 WAKU_EXPERIMENTAL。
    if getattr(settings, "experimental", False) or os.getenv("WAKU_EXPERIMENTAL", "") in ("1", "true", "yes"):
        from waku.tools import experimental  # 延迟导入：未启用时不加载该模块

        for t in experimental.make_tools(settings):  # 依次注册所有实验性工具
            registry.register(t)

    # 苹果生态的读取/写入工具（可选启用；首次使用会触发 macOS 权限提示）。
    if settings.apple_tools:
        from waku.tools import apple  # 延迟导入：非 macOS 平台不加载

        for t in apple.make_tools():
            registry.register(t)

    # MCP 服务器（通过 .waku/mcp.json 可选启用）。
    mcp_config = settings.home / "mcp.json"  # 配置文件路径：用户数据目录下的 mcp.json
    if mcp_config.exists():  # 有配置才连接；没配 MCP 保持零开销
        try:
            from waku.tools.mcp_client import MCPBridge  # 延迟导入：不把 mcp 包变成硬依赖

            bridge = MCPBridge(mcp_config)
            for t in bridge.start():  # 连接各服务器并把它们的工具拉进来
                registry.register(t)
            registry.mcp_bridge = bridge  # 这样 Waku.close() 就能停掉这些服务器
        except ImportError:
            # 可选依赖 mcp 没装：打印提示而不是崩溃，Waku 照常启动。
            print("mcp.json found but the 'mcp' package is missing — pip install 'waku-agent[mcp]'")

    return registry  # 返回装配好的注册表，交给循环/网关调用
