"""
show trace json
"""

from __future__ import annotations

import argparse      # 命令行参数解析
import json          # 解析追踪的每一行
import sys           # 重配 stdout 编码（Windows GBK 控制台遇到 emoji 会崩）
from pathlib import Path
from typing import TextIO

from rich.console import Console

from waku.config import load_settings


def _short(value: object, limit: int = 100) -> str:
    """让追踪字段可读，又不让单条事件占满整个屏幕。"""
    if isinstance(value, str):
        text = value.replace("\n", " ")      # 字符串直接拍平换行
    else:
        text = json.dumps(value, ensure_ascii=False, default=str, separators=(", ", ": "))
        # 非字符串（如 dict）序列化成紧凑 JSON：保留中文、兜底 str()、去掉多余空格
    return text if len(text) <= limit else text[:limit - 1] + "…"   # 超长截断加省略号


def _event_summary(event: dict) -> str:
    """描述 Tracer 和循环观察者发出的事件字段。"""
    kind = event.get("type", "event")        # 事件类型；缺省叫 "event"
    if kind == "turn_start":                 # 轮次开始：显示用户消息
        return f"turn start · {_short(event.get('user_message', ''))}"
    if kind == "turn_end":                   # 轮次结束：回复 + 迭代次数
        reply = _short(event.get("reply", ""))
        return f"turn end · {reply} · {event.get('iterations', 0)} iteration(s)"
    if kind == "llm":                        # LLM 调用：显示 token 用量
        usage = event.get("usage", {})
        return (f"llm · iteration {event.get('iteration', '?')} · "
                f"{usage.get('in', 0)} in / {usage.get('out', 0)} out")
    if kind == "tool":                       # 工具调用：工具名(参数) → 输出
        return f"tool · {event.get('tool', '?')}({_short(event.get('args', {}))}) → {_short(event.get('output', ''))}"
    if kind == "gate":                       # 门禁决策：decision + 可选 reason
        reason = event.get("reason")
        return f"gate · {event.get('decision', '?')}" + (f" — {_short(reason)}" if reason else "")
    if kind == "consolidation":              # 记忆整合：新增事实数
        return f"memory · consolidated {event.get('new_facts', 0)} fact(s)"

    fields = {key: value for key, value in event.items() if key not in {"type", "ts"}}
    # 未识别的类型：把除 type/ts 外的字段整体展示，避免漏信息
    return str(kind) + (f" · {_short(fields)}" if fields else "")


def render_trace(path: Path, console: Console | None = None) -> int:
    """渲染一个追踪文件，并返回已打印的有效事件数。"""
    console = console or Console()
    console.print(f"[bold]Trace[/bold] {path}")   # 标题行
    events = 0
    turn_depth = 0                                # 缩进层级（0=顶格，1=轮次内）

    try:
        lines: TextIO = path.open(encoding="utf-8")
    except FileNotFoundError:                     # 文件不存在时给出提示并退出
        console.print(f"[dim]No trace file found: {path}[/dim]")
        return 0

    with lines:
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():                  # 空行跳过
                continue
            try:
                event = json.loads(line)          # 解析这一行 JSON
            except json.JSONDecodeError:          # 坏行：提示并继续，不让渲染崩溃
                console.print(f"[yellow]Skipping invalid JSON on line {line_number}.[/yellow]")
                continue
            if not isinstance(event, dict):       # 非对象 JSON（如裸数字）也跳过
                console.print(f"[yellow]Skipping non-object JSON on line {line_number}.[/yellow]")
                continue

            kind = event.get("type", "event")
            # 对话轮次从不嵌套，所以深度是二值的：turn_start/turn_end 这一对
            # 总是顶格靠左，它们之间的事件缩进一级。用「赋值」而不是「累加」深度，
            # 能避免一场在写完 turn_end 之前就崩溃的对话把当天其余内容不断向右推。
            if kind in {"turn_start", "turn_end"}:
                turn_depth = 0                    # 轮次边界归零，杜绝累积
            timestamp = str(event.get("ts", ""))
            stamp = timestamp[11:19] if len(timestamp) >= 19 else timestamp  # 取 HH:MM:SS
            indent = "  " * turn_depth            # 按深度生成缩进
            console.print(f"{indent}[dim]{stamp}[/dim] {_event_summary(event)}")
            if kind == "turn_start":              # 进入轮次后，后续事件缩进一级
                turn_depth = 1
            events += 1

    if not events:                                # 文件里没有任何有效事件
        console.print("[dim]Trace is empty.[/dim]")
    return events


def latest_trace(traces: Path) -> Path | None:
    """返回最近的每日追踪，且不读取它的全部内容。"""
    if not traces.is_dir():                       # 追踪目录不存在
        return None
    files = (path for path in traces.glob("*.jsonl") if path.is_file())   # 只看文件，不看目录

    return max(files, key=lambda path: path.stat().st_mtime, default=None)  # 按修改时间取最新


def main() -> None:
    # Windows 控制台默认 GBK，trace 里的 emoji（👋 等）无法编码会导致 rich 崩溃；
    # 强制 stdout 用 UTF-8。Windows Terminal / VS Code 终端是 UTF-8，能正常显示。
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Render a Waku JSONL trace as a terminal timeline.")
    parser.add_argument("trace", nargs="?", type=Path, help="trace JSONL file (defaults to latest)")
    args = parser.parse_args()

    if args.trace:                                # 显式指定了文件
        render_trace(args.trace)
        return

    traces = load_settings().home / "traces"      # 否则读默认 home 下的追踪目录
    print(traces)
    path = latest_trace(traces)                   # 自动选最近的一个文件
    print(path)
    if path is None:                              # 一个追踪都没有
        Console().print(f"[dim]No traces found in {traces}.[/dim]")
        return
    render_trace(path)


if __name__ == "__main__":
    main()
