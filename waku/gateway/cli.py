"""CLI 网关 —— 与你的 Waku 对话的零配置方式。

网关接口盒子：网关只负责文本的进与出；所有有趣的事情都发生在循环里。
Telegram 网关是同样约 60 行代码，只是用轮询代替 input()。
"""

from __future__ import annotations  # 让类型注解（如 sqlite3.Connection）在旧版 Python 里也能用

import sqlite3  # 直接读状态库（.waku/state.db），做记忆快照查询

from rich.console import Console  # Rich：在终端渲染彩色/带样式文本
from rich.panel import Panel      # 带边框的卡片容器，用于包住记忆快照
from rich.text import Text        # 支持样式的文本对象（Panel 内展示需要它）

from waku.app import Waku  # 核心应用入口：会话、循环、记忆都经它

console = Console()  # 全局富文本控制台，供所有打印复用


def _memory_snapshot(conn: sqlite3.Connection) -> str:
    """渲染 Waku 本地记忆的一个有界、只读视图。"""
    fact_count = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]  # 语义事实总数（取首行首列）
    facts = conn.execute("SELECT subject, content FROM facts ORDER BY id DESC LIMIT 8").fetchall()  # 最新 8 条事实
    episode_count = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]  # 情景记忆（episodes）总数
    episodes = conn.execute(
        "SELECT happened_at, summary FROM episodes ORDER BY happened_at DESC, id DESC LIMIT 5"
    ).fetchall()  # 最新 5 段情景摘要
    pending = conn.execute("SELECT COUNT(*) FROM chat_log WHERE consolidated = 0").fetchone()[0]  # 尚未并入记忆的聊天条数

    lines = [f"Semantic facts ({fact_count})"]  # 区块标题：语义事实
    lines.extend(f"- [{row['subject']}] {row['content']}" for row in facts)  # 逐条渲染「主题 - 内容」
    if not facts:
        lines.append("- none yet")  # 空库时的占位提示

    lines.extend(["", f"Recent episodes ({episode_count})"])  # 空行分隔后再加情景标题
    lines.extend(f"- {row['happened_at']} - {row['summary']}" for row in episodes)  # 逐条渲染「时间 - 摘要」
    if not episodes:
        lines.append("- none yet")

    lines.extend(["", f"Unconsolidated chat messages: {pending}"])  # 待整合消息数是记忆健康度的参考指标
    return "\n".join(lines)  # 拼成一段多行纯文本，供 Panel 整块展示


def _observer(kind: str, event: dict) -> None:
    """实时展示循环的内部——视频里'透明框架'的那段。"""
    if kind == "tool":  # 工具调用事件
        console.print(f"  [dim]tool · {event['tool']}({event['args']}) → {event['output'][:80]}[/dim]")  # 输出截断到 80 字符，避免刷屏
    elif kind == "gate":  # 检索门控决策事件
        console.print(f"  [dim]gate · {event['decision']} — {event.get('reason','')}[/dim]")  # get 兜底：reason 不是每次都有
    elif kind == "consolidation":  # 记忆整合完成事件
        console.print(f"  [dim]memory · consolidated {event['new_facts']} fact(s) from recent chats[/dim]")  # 显示本次新沉淀出的事实数


def main() -> None:
    waku = Waku()  # 启动核心应用：加载记忆、会话与模型配置
    waku.session.session_id = "terminal"   # 收件箱里它自己的对话线程
    console.print(Panel.fit(  # 开机横幅；fit 让边框贴合内容宽度
        "[bold]Waku[/bold] — local, yours, transparent.\n"
        f"home: {waku.settings.home.resolve()}   model: {waku.settings.model}\n"
        "Commands: /memory · /quit",
        border_style="cyan",
    ))
    while True:  # 主事件循环：阻塞等输入，处理完再回来
        try:
            user_message = console.input("[bold cyan]you ›[/bold cyan] ").strip()  # 读一行输入并去掉首尾空白
        except (EOFError, KeyboardInterrupt):  # Ctrl-D / Ctrl-C：优雅退出
            break
        if not user_message:  # 空输入（只按回车）直接忽略
            continue
        if user_message in ("/quit", "/exit"):  # 退出命令
            break
        if user_message == "/memory":  # 记忆快照命令：纯本地查询，不进模型
            console.print(
                Panel(
                    Text(_memory_snapshot(waku.conn)),  # 把纯文本快照包成可样式化对象
                    title="Memory snapshot",
                    border_style="cyan",
                )
            )
            continue
        result = waku.respond(user_message, observer=_observer, source="cli")  # 送入循环；observer 让内部事件实时可见
        console.print(f"[bold green]waku ›[/bold green] {result.reply}\n")  # 打印模型回复
    console.print("[dim]bye — your memory stays in state.db[/dim]")  # 退出提示：记忆已持久化、未被清空


if __name__ == "__main__":
    main()  # 仅当直接运行该文件（而非被 import）时才启动
