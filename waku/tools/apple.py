"""苹果生态工具（macOS），让 Waku 能向你汇报真实的周日程——
读取你实际的日历应用（包括邮件邀请的事件）和邮件，并可写入提醒事项/备忘录。
通过 WAKU_APPLE_TOOLS=1 启用；首次使用会触发系统的"自动化"权限提示。
所有 AppleScript 都带超时运行，并返回真实的错误文本，
这样慢或未授权的调用绝不会卡住一轮对话。
"""

from __future__ import annotations  # 让类型注解（如 tuple[bool, str]）在旧版 Python 里也能用

import os         # 读取 WAKU_APPLE_CALENDARS 环境变量
import subprocess # 运行 osascript 命令与苹果应用交互
import sys        # 判断平台（sys.platform == 'darwin' 才是 macOS）
import time       # 缓存过期判断需要当前时间戳

from waku.tools.registry import Tool  # 工具定义类

_TIMEOUT = 30  # osascript 单次调用的默认超时（秒）：防止权限弹窗/慢调用卡住循环
_cache: dict[str, tuple[float, str]] = {}  # 缓存：key → (写入时刻, 结果文本)，键如 "cal:7"


def _osa(script: str, timeout: int = _TIMEOUT) -> tuple[bool, str]:
    """执行一段 AppleScript，返回 (是否成功, 输出文本)。失败也返回文本而不是抛异常。"""
    if sys.platform != "darwin":  # 非 macOS 平台没有 osascript，直接判失败
        return False, "Apple tools are macOS-only."
    try:
        # 运行 osascript；text=True 拿到字符串而不是字节
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:  # 超时 = 通常系统正弹着权限对话框
        return False, "timed out — the app may be showing a permission dialog; approve it and retry."
    except OSError as exc:  # osascript 不存在或无法启动
        return False, f"could not run osascript ({exc})"
    if r.returncode != 0:  # AppleScript 自身报错（如未授权）；错误文本截到 200 字符
        return False, (r.stderr or "failed").strip()[:200]
    return True, r.stdout.strip()  # 成功：返回脚本 stdout


def _cached(key: str, ttl: int, producer) -> str:
    """带 TTL 的内存缓存：TTL 内重复调用直接返回旧结果，避免反复触发 macOS 权限提示。"""
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:  # 命中且未过期：直接用缓存
        return hit[1]
    val = producer()  # 未命中/过期：真实调用一次再填充缓存
    _cache[key] = (now, val)
    return val


def read_apple_calendar(days_ahead: int = 7) -> str:
    """从现在到 days_ahead 之间日历应用中的事件。用 WAKU_APPLE_CALENDARS=Work,Home
    来限定要读取的日历（在日历很多时枚举会很慢）。"""
    def go() -> str:  # 实际读取逻辑；作为 producer 传给 _cached
        cals = os.getenv("WAKU_APPLE_CALENDARS", "").strip()  # 用户用环境变量限定要读的日历名
        cal_clause = ""
        if cals:  # 有限定：拼出"不在列表则跳过"的 AppleScript 条件
            names = " or ".join(f'name of cal is "{c.strip()}"' for c in cals.split(","))
            cal_clause = f"if not ({names}) then error number -128"
        script = f'''
set out to ""
set startDate to current date
set endDate to (current date) + ({int(days_ahead)} * days)
tell application "Calendar"
  repeat with cal in calendars
    try
      {cal_clause}
      set evs to (every event of cal whose start date ≥ startDate and start date ≤ endDate)
      repeat with e in evs
        set out to out & (name of cal) & " | " & (summary of e) & " | " & ((start date of e) as string) & linefeed
      end repeat
    end try
  end repeat
end tell
return out'''
        ok, res = _osa(script, timeout=45)  # 日历枚举可能较慢，放宽超时到 45 秒
        return res if ok else f"Calendar unavailable: {res}"  # 失败如实回报，不让循环瞎猜
    return _cached(f"cal:{days_ahead}", 600, go) or "No events in that window."  # 缓存 10 分钟


def read_apple_mail(hours: int = 48, limit: int = 20) -> str:
    """最近的邮件消息：主题、发件人、日期，以及一个能精确定位到
    该邮件并打开 Mail 的 message:// 链接。"""
    def go() -> str:
        script = f'''
set out to ""
set cutoff to (current date) - ({int(hours)} * hours)
tell application "Mail"
  set box to inbox
  set msgs to (messages of box whose date received ≥ cutoff)
  set n to 0
  repeat with m in msgs
    if n ≥ {int(limit)} then exit repeat
    set out to out & (subject of m) & " | " & (sender of m) & " | " & ((date received of m) as string) & " | message://%3c" & (message id of m) & "%3e" & linefeed
    set n to n + 1
  end repeat
end tell
return out'''
        ok, res = _osa(script, timeout=45)
        return res if ok else f"Mail unavailable: {res}"
    return _cached(f"mail:{hours}:{limit}", 300, go) or "No recent mail."  # 缓存 5 分钟


def create_reminder(title: str, due: str = "") -> str:
    props = f'name:"{title}"'  # AppleScript 记录：至少要带 name 字段
    if due:  # 给了到期时间就拼上 due date（date 解析依赖系统区域设置）
        props += f', due date:(date "{due}")'
    ok, res = _osa(f'tell application "Reminders" to make new reminder with properties {{{props}}}')
    return f"Reminder created: {title}" if ok else f"Reminder failed: {res}"  # 成败都如实转述


def create_note(title: str, body: str = "") -> str:
    safe = (title + "\n" + body).replace('"', "'")  # 双引号换成单引号，避免破坏 AppleScript 字符串字面量
    ok, res = _osa(f'tell application "Notes" to make new note at folder "Notes" with properties {{body:"{safe}"}}')
    return f"Note created: {title}" if ok else f"Note failed: {res}"


def make_tools() -> list[Tool]:
    return [
        Tool("read_apple_calendar",
             # 提示词：读取用户真实的 macOS 日历未来 N 天的事件（含邮件邀请的事件）。
             # 用于每周/每日简报和"我的日历上有什么"。
             "Read the user's real macOS Calendar for the next N days (includes events invited by email). Use for weekly/daily briefings and 'what's on my calendar'.",
             {"type": "object", "properties": {"days_ahead": {"type": "integer",
                 # 提示词：默认 7 天
                 "description": "default 7"}}},
             lambda days_ahead=7: read_apple_calendar(int(days_ahead))),  # 默认 7 天；int() 兜住字符串入参
        Tool("read_apple_mail",
             # 提示词：读取用户最近的 Apple 邮件（主题、发件人、日期，以及可逐封打开的
             # message:// 链接）。用于向用户汇报哪些事情需要关注。
             "Read the user's recent Apple Mail (subject, sender, date, and a message:// link to open each). Use to brief the user on what needs attention.",
             {"type": "object", "properties": {"hours": {"type": "integer",
                 # 提示词：回溯窗口，默认 48 小时
                 "description": "look-back window, default 48"}}},
             lambda hours=48: read_apple_mail(int(hours))),  # 默认 48 小时
        Tool("create_reminder",
             # 提示词：在 Apple 提醒事项中创建一个提醒。
             "Create a reminder in Apple Reminders.",
             {"type": "object", "properties": {"title": {"type": "string"}, "due": {"type": "string",
                 # 提示词：可选，例如 "Monday 9:00 AM"
                 "description": 'optional, e.g. "Monday 9:00 AM"'}}, "required": ["title"]},
             lambda title, due="": create_reminder(title, due)),  # 标题必填，到期时间可省略
        Tool("create_note",
             # 提示词：在 Apple 备忘录中创建一个笔记。
             "Create a note in Apple Notes.",
             {"type": "object", "properties": {"title": {"type": "string"}, "body": {"type": "string"}}, "required": ["title"]},
             lambda title, body="": create_note(title, body)),  # 标题必填，正文可省略
    ]
