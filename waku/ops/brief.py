"""`python -m waku brief` —— 晨间简报：像普通对话一样走一遍完整的框架/运行时（所以它和任何一次对话一样会留下追踪记录、也能被动画演示），把你的真实日历、邮件和记忆整合成一份「以重点为先」的摘要。可以用 cron 把它排成每日问候：

    30 7 * * *  cd ~/waku-agent && make brief

繁重的处理逻辑放在 skills/weekly-brief/SKILL.md 里——这里只是发起对话并把结果保存到 outbox。
"""

from __future__ import annotations  # 让类型注解在旧版 Python 里也能正常求值

from datetime import date          # 给输出文件打上「今天」的日期戳

from rich.console import Console   # 终端彩色输出

from waku.app import Waku          # 应用入口：跑一次真实的对话轮次


# 简报提示词（发给 LLM 的英文原文，保持原样）：让 Waku 概括本周的日历、
# 需要留意的邮件，以及今天应该专注的事。这就是整个模块的任务输入。
PROMPT = "Brief me on my week: what's on my calendar, what's in my mail that needs attention, and what I should focus on today."


def main() -> None:
    console = Console()                     # 所有输出都经由 rich，保证终端上美观可读
    waku = Waku()                           # 构建一个完整的 Waku 实例（框架/运行时）
    if not waku.settings.apple_tools:       # 未开启 Apple 工具时，简报读不到真实日历/邮件
        console.print("[dim]Tip: set WAKU_APPLE_TOOLS=1 to brief from your real Calendar and Mail.[/dim]")
    result = waku.respond(PROMPT, source="brief")   # 以「brief」为来源跑一轮真实对话
    console.print(result.reply)             # 把智能体的回复打印到终端
    out = waku.settings.home / "outbox" / f"brief-{date.today().isoformat()}.txt"  # 按日期命名的落盘路径
    out.write_text(result.reply + "\n")     # 写入 outbox，供其它网关 / 动画演示读取
    console.print(f"[dim]saved to {out}[/dim]")     # 告知用户文件保存位置


if __name__ == "__main__":
    main()                                  # 命令行入口：python -m waku brief
