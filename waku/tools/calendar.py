"""create_event "会议触发了吗？" 是关键的确定性评估：
要么写入了正确的行，要么没有。
事件落在哪里：
  总是        state.db（评估在此断言）+ calendar.ics（可导入的文件）
  可选        通过 AppleScript 同步到专用的 "Waku" 日历——
              设置 WAKU_APPLE_CALENDAR=1。首次使用会让 macOS 请求
              你的终端控制日历的权限；批准一次即可。

该工具的返回字符串总是明确说明事件去了哪里——模型会原样转述，
所以 Waku 永远不会夸大实际发生的事情。
"""

from __future__ import annotations  # 让类型注解在旧版 Python 里也能用

import sqlite3    # 写入本地 state.db
import subprocess # 运行 osascript 把事件同步到 Apple 日历
import sys        # 判断平台（非 macOS 跳过同步）
from datetime import datetime  # 解析/格式化 ISO 时间戳
from pathlib import Path       # 处理日历 ICS 文件路径

from waku.tools.registry import Tool  # 工具定义类

APPLE_CALENDAR_NAME = "Waku" # 同步用的目标日历名（首次使用会自动创建）


def _write_ics(home: Path, title: str, start: str, end: str, attendees: str) -> None:
    """追加一个最小的 VEVENT。像 2026-07-14T09:00 这样的 ISO 时间戳会变成
    ICS 的紧凑形式 20260714T090000。"""
    ics_path = home / "calendar.ics" # 可手动导入日历应用的 ICS 文件路径

    def dt(s: str) -> str:
        # ICS 时间戳要去掉 - 和 :，16 位（缺秒）时补上 "00"。
        return s.replace("-", "").replace(":", "") + ("00" if len(s) == 16 else "")

    event = (  # 一个最小的 VEVENT 块（ICS 纯文本）
        "BEGIN:VEVENT\n"
        f"SUMMARY:{title}\n"
        f"DTSTART:{dt(start)}\n"
        f"DTEND:{dt(end)}\n"
        f"DESCRIPTION:attendees: {attendees}\n"
        "END:VEVENT\n"
    )
    if ics_path.exists():
        body = ics_path.read_text().replace("END:VCALENDAR\n", "")  # 剥掉旧收尾，把新事件插进去
    else:
        body = "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//waku-agent//EN\n"  # 首次写入：先给文件头
    ics_path.write_text(body + event + "END:VCALENDAR\n")  # 事件后面重新补上收尾标记


def _applescript_date(var: str, iso: str) -> str:
    """从 ISO 各分量构造一个 AppleScript 日期——不受系统区域设置影响
    （绝不要给 AppleScript 传格式化后的日期字符串；其解析依赖区域设置）。"""
    d = datetime.fromisoformat(iso)  # 解析 ISO 时间戳
    # 在设置月/年之前先把 day 设为 1：避免经典的 AppleScript 溢出
    #（如果今天是 31 号，把月份设为只有 30 天的月会滚到下个月）
    return (
        f"set {var} to current date\nset day of {var} to 1\n"
        f"set year of {var} to {d.year}\nset month of {var} to {d.month}\n"
        f"set day of {var} to {d.day}\nset hours of {var} to {d.hour}\n"
        f"set minutes of {var} to {d.minute}\nset seconds of {var} to 0\n"
    )


def sync_to_apple_calendar(title: str, start: str, end: str, notes: str = "") -> str:
    """在日历应用中、'Waku' 日历（首次使用时会创建）下创建事件。
    返回一段简短、人类可读的结果供工具输出。"""
    if sys.platform != "darwin":  # 非 macOS：直接跳过，返回说明而不是报错
        return "Apple Calendar sync skipped (not macOS)."
    safe_title = title.replace("\\", "").replace('"', "'")  # 清掉反斜杠/双引号，避免破坏 AppleScript 字符串
    safe_notes = notes.replace("\\", "").replace('"', "'")
    # 优先使用专用的 "Waku" 日历，但 macOS 无法通过 AppleScript 在
    # 仅 iCloud 的账户中创建日历——回退到第一个可写的日历，
    # 并报告实际使用的是哪一个。
    script = (
        _applescript_date("startDate", start)  # 先构造事件起止两个日期变量
        + _applescript_date("endDate", end)
        + f'''
tell application "Calendar"
  if not (exists calendar "{APPLE_CALENDAR_NAME}") then  -- 尚无 Waku 日历则尝试创建
    try
      make new calendar with properties {{name:"{APPLE_CALENDAR_NAME}"}}
      delay 1
    end try
  end if
  if exists calendar "{APPLE_CALENDAR_NAME}" then
    set targetCal to calendar "{APPLE_CALENDAR_NAME}"   -- 建好了就用专用的
  else
    set targetCal to first calendar whose writable is true  -- 建不了就退回第一个可写日历
  end if
  tell targetCal
    make new event with properties {{summary:"{safe_title}", start date:startDate, end date:endDate, description:"{safe_notes}"}}
  end tell
  return name of targetCal   -- 返回实际用到的日历名，供下方报告
end tell'''
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=30
        )
    except subprocess.TimeoutExpired:  # 卡住通常是 macOS 在弹权限对话框；事件仍安全在本地
        return (
            "Apple Calendar sync timed out — this usually means macOS is showing a "
            "permission dialog ('would like to add to your Calendar'). The event is safe "
            "in the local calendar; approve the dialog and ask me to create it again."
        )
    except OSError as exc:  # osascript 无法启动
        return f"Apple Calendar sync FAILED ({exc}) — the event is still in the local calendar."
    if result.returncode != 0:  # AppleScript 报错（多半是权限被拒）
        detail = (result.stderr or "").strip()[:120]  # 错误文本截到 120 字符，别把整屏错误塞给模型
        return (
            f"Apple Calendar sync FAILED ({detail}) — the event is still in the local "
            "calendar. If this is a permissions error, allow your terminal to control "
            "Calendar in System Settings > Privacy & Security > Automation."
        )
    used = (result.stdout or "").strip() or APPLE_CALENDAR_NAME  # 没回显日历名则默认用上了 Waku 日历
    return f"Also added to Apple Calendar (calendar '{used}')."


def make_tool(conn: sqlite3.Connection, home: Path, apple_calendar: bool = False) -> Tool:
    """构造 create_event 工具：写入本地数据库，可选同步 Apple 日历。"""
    def create_event(title: str = "", start: str = "", end: str = "", attendees: str = "", notes: str = "") -> str:
        # 防御性措施：模型有时会发出空/不完整的工具调用。返回一个
        # 模型能恢复的有用消息，而不是原始的 Python TypeError。
        if not title or not start:  # 缺标题或开始时间：提示补全，避免空数据入库
            return ("create_event needs at least a title and a start time "
                    "(ISO 8601, e.g. 2026-07-14T09:00). Please call it again with both.")
        if not end:
            # 默认：一小时
            from datetime import timedelta  # 局部导入：只在需要默认时长时才引入
            end = (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat(timespec="minutes")

        # 幂等性保护：相同的标题+开始时间 = 同一个事件。混乱的模型
        #（或没耐心的用户）绝不能重复预订同一个会议。
        start = start[:16]  # 归一化 2026-07-11T17:00:00 → 2026-07-11T17:00，保证查重/插入一致
        end = end[:16]
        existing = conn.execute(  # 按「标题+开始时间」查重
            "SELECT id FROM calendar_events WHERE title = ? AND start = ?", (title, start)
        ).fetchone()
        if existing:  # 已存在：直接返回提示而不插入（幂等保证）
            return f"Event '{title}' at {start} already exists (not duplicated)."

        conn.execute(  # 写入 state.db（确定性评估在此断言）
            'INSERT INTO calendar_events (title, start, "end", attendees, notes) VALUES (?,?,?,?,?)',
            (title, start, end, attendees, notes),
        )
        conn.commit()  # 提交事务，确保落盘
        _write_ics(home, title, start, end, attendees)  # 同步追加到可导入的 ICS 文件

        where = f"Saved to the local calendar ({home / 'calendar.ics'})."  # 事件落点说明（模型会转述）
        if apple_calendar:  # 配置开了同步：尝试写入 Apple 日历
            where += " " + sync_to_apple_calendar(title, start, end, notes)
        else:  # 没开：如实说明，并给出手动导入途径
            where += (
                " Not synced to any calendar app (enable with WAKU_APPLE_CALENDAR=1, "
                f"or import manually: open {home / 'calendar.ics'})."
            )
        return (
            f"Event created: '{title}' {start} → {end}"  # 时间区间，模型会转述给用户
            + (f" with {attendees}" if attendees else "")
            + f". {where}"
        )

    return Tool(
        name="create_event",
        description=(
            # 提示词：在用户的本地日历上创建一个日历事件。只要用户想在
            # 特定时间安排、预订或计划某件事时使用。
            "Create a calendar event on the user's local calendar. Use whenever the user "
            "wants to schedule, book, or plan something at a specific time."
        ),
        input_schema={
            "type": "object",
            "properties": {
                # 提示词：简短的事件标题
                "title": {"type": "string", "description": "Short event title"},
                # 提示词：开始时间，ISO 8601，例如 2026-07-14T09:00
                "start": {"type": "string", "description": "Start time, ISO 8601, e.g. 2026-07-14T09:00"},
                # 提示词：结束时间，ISO 8601。默认是开始时间 + 1 小时。
                "end": {"type": "string", "description": "End time, ISO 8601. Defaults to start + 1h."},
                # 提示词：逗号分隔的名字/邮箱
                "attendees": {"type": "string", "description": "Comma-separated names/emails"},
                # 提示词：事件的补充背景说明（可选）
                "notes": {"type": "string", "description": "Optional context for the event"},
            },
            "required": ["title", "start"],
        },
        fn=create_event,  # 闭包作为执行函数绑定到工具
    )


def make_list_tool(conn: sqlite3.Connection) -> Tool:
    """list_events —— 日历的读侧。create_event 负责写入；没有它，
    代理就只能预订，永远答不上"我的日历上有什么？"。"""
    def list_events(start: str = "", end: str = "", limit: int = 20) -> str:
        query = 'SELECT title, start, "end", attendees FROM calendar_events'  # 基查询：只取展示需要的列
        clauses, params = [], []  # 动态 WHERE 子句与对应参数，按需拼接
        if start:
            clauses.append("start >= ?")      # 下界：开始时间之后（含当天）
            params.append(start[:10])                 # 从那天开始，含当天
        if end:
            clauses.append("start <= ?")      # 上界：开始时间之前（含当天）
            params.append(end[:10] + "T23:59")        # 直到那天结束，含当天
        if clauses:  # 有过滤条件才拼 WHERE（参数化查询，避免注入）
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY start LIMIT ?"  # 按开始时间升序，限制返回条数
        params.append(max(1, min(int(limit or 20), 100)))  # limit 夹在 1..100，防御乱传参数
        rows = conn.execute(query, params).fetchall()  # 执行查询
        if not rows:  # 空结果也要说明是读了本地日历，避免误导
            window = f" between {start} and {end}" if (start or end) else ""
            return f"No events found{window}. (This reads the local calendar in .waku/state.db.)"
        lines = ["Events on the local calendar:"]  # 结果表头
        for r in rows:  # 每行渲染成一行文本
            who = f" with {r['attendees']}" if r["attendees"] else ""
            lines.append(f"- {r['title']}: {r['start']} → {r['end']}{who}")
        return "\n".join(lines)  # 拼成整段文本返回给模型

    return Tool(
        name="list_events",
        description=(
            # 提示词：读取用户的日历：列出事件，可选指定日期范围。当用户问
            # 某天/某周/昨天等日历或日程上有什么时使用。日期用 ISO 格式
            #（例如 2026-07-10）；两者都省略则列出所有即将到来的事件。
            # 对于"昨天/今天"要从系统提示词中给出的当前时间解析日期。
            "Read the user's calendar: list events, optionally within a date range. "
            "Use whenever the user asks what's on their calendar / schedule for a day, "
            "week, yesterday, etc. Dates are ISO (e.g. 2026-07-10); omit both to list "
            "everything upcoming. For 'yesterday'/'today' resolve the date from the "
            "current time given in your system prompt."
        ),
        input_schema={
            "type": "object",
            "properties": {
                # 提示词：要包含的最早日期，ISO 格式（例如 2026-07-10）
                "start": {"type": "string", "description": "earliest date to include, ISO (e.g. 2026-07-10)"},
                # 提示词：要包含的最晚日期，ISO 格式（例如 2026-07-10）
                "end": {"type": "string", "description": "latest date to include, ISO (e.g. 2026-07-10)"},
                # 提示词：返回的最大事件数（默认 20）
                "limit": {"type": "integer", "description": "max events to return (default 20)"},
            },
            "required": [],
        },
        fn=list_events,
    )
