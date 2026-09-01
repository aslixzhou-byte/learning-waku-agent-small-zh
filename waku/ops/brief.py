"""`python -m waku brief` 晨间简报：像普通对话一样走一遍完整的框架/运行时（所以它和任何一次对话一样会留下追踪记录、也能被动画演示），把你的真实日历、邮件和记忆整合成一份「以重点为先」的摘要。可以用 cron 把它排成每日问候：
繁重的处理逻辑放在 skills/weekly-brief/SKILL.md 里——这里只是发起对话并把结果保存到 outbox。
"""

from __future__ import annotations
from datetime import date
from rich.console import Console
from waku.app import Waku

# 简报提示词（发给 LLM 的英文原文，保持原样）：让 Waku 概括本周的日历、
# 需要留意的邮件，以及今天应该专注的事。这就是整个模块的任务输入。
PROMPT = "Brief me on my week: what's on my calendar, what's in my mail that needs attention, and what I should focus on today."

def main() -> None:
    console = Console()
    waku = Waku()
    if not waku.settings.apple_tools:
        console.print("[dim]Tip: set WAKU_APPLE_TOOLS=1 to brief from your real Calendar and Mail.[/dim]")
    result = waku.respond(PROMPT, source="brief")
    console.print(result.reply)
    out = waku.settings.home / "outbox" / f"brief-{date.today().isoformat()}.txt"
    out.write_text(result.reply + "\n")
    console.print(f"[dim]saved to {out}[/dim]")


if __name__ == "__main__":
    main() # python -m waku brief
