"""把 .waku 重置为演示 / 录制所需的干净、精选状态。

    python scripts/demo_seed.py                 # 干净状态，保留消费账本
    python scripts/demo_seed.py --reset-spend   # 同时清空 usage.jsonl（钱/令牌）

它做什么（你的旧状态会先备份，绝不会只是删除）：
  1. 把当前 .waku 移到 .waku.bak-<时间戳>
  2. 创建全新的 state.db + calendar.ics
  3. 预置少量干净的记忆（几条事实 + 一条情景）和 ONE 个日历事件——
     Sergey 每周六下午 5 点的固定游泳
  4. 清空循环/工具追踪以及 Ops 评估历史，让 Loop、Tools 和 Ops 标签页
     从空白开始，在你输入时当着观看者实时填充

金钱/令牌消费账本（usage.jsonl）被视为永久记录，默认保留——只有显式传入
--reset-spend 才会被清空。

它写入的一切与应用写入的数据相同——之后打开 state.db 看起来就和真实使用
一模一样，只是更整洁。
"""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime

from waku.config import load_settings
from waku.db import connect
from waku.memory.episodic.store import SqliteEpisodeStore
from waku.memory.semantic.store import SqliteFactStore
from waku.tools.calendar import make_tool

# 精选预置数据——干净、无重复。录制前可按需编辑。
FACTS = [
    ("user", "The user runs the YouTube channel 'Sean's AI Stories' and films implementation "
             "walkthroughs. His X account is @ShenSeanChen. All of his Chinese social media "
             "accounts are called 肖恩君Sean."),
    ("raj", "Raj is a close friend who plays really great tennis and always teaches me great "
            "British slangs!"),
    ("sergey", "Sergey is the close friend who loves swimming and often cooks delicious food!"),
]
EPISODE = ("2026-07-11", "Confirmed the standing Saturday 5 PM swim with Sergey.")
EVENT = {"title": "Swim with Sergey", "start": "2026-07-11T17:00",
         "end": "2026-07-11T18:00", "attendees": "Sergey"}


def main(reset_spend: bool = False) -> None:
    settings = load_settings()
    home = settings.home

    if home.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = home.with_name(f"{home.name}.bak-{stamp}")
        shutil.copytree(home, backup)
        print(f"backed up {home} -> {backup}")
        # calendar.ics 和这些目录都是没有任何进程占用的普通文件。
        # traces/ = Loop 与 Tools 的历史；清空它让这些标签页从空白开始。
        (home / "calendar.ics").unlink(missing_ok=True)
        for sub in ("outbox", "skills", "traces"):
            d = home / sub
            if d.exists():
                shutil.rmtree(d)
        # Ops 评估历史——从空白开始，这样一次真实的 `make gate` 会新增可见的一行。
        (home / "eval_runs.jsonl").unlink(missing_ok=True)
        (home / "eval_report.json").unlink(missing_ok=True)
        # 消费账本是永久记录——仅在显式请求时清空。
        if reset_spend:
            (home / "usage.jsonl").unlink(missing_ok=True)

    settings.ensure_home()
    conn = connect(home)

    # 原地清空数据库行——绝不删除 state.db。删除文件会让任何存活的网关
    # （正在运行的 `make telegram`、dashboard、打开的 CLI）持有一条指向旧 inode
    # 的已损坏、只读连接。
    for table in ("chat_log", "calendar_events", "facts", "episodes"):
        conn.execute(f"DELETE FROM {table}")   # 触发器让 FTS 索引保持同步
    conn.commit()

    facts, episodes = SqliteFactStore(conn), SqliteEpisodeStore(conn)
    for subject, content in FACTS:
        facts.add(subject, content, source="user")
    episodes.add(EPISODE[1], happened_at=EPISODE[0])

    create_event = make_tool(conn, home).fn
    print(create_event(**EVENT))

    # 为全新状态重新生成人类可读的 MEMORY.md 镜像
    from waku.memory import Memory

    Memory(conn, settings, None).export_markdown()

    print(f"\nclean demo state ready in {home}")
    print(f"  facts: {len(FACTS)}  ·  episodes: 1  ·  events: 1  ·  chat log: cleared")
    print("  CLEARED: loop/tool traces, Ops eval history, outbox, skills.")
    if reset_spend:
        print("  CLEARED: usage.jsonl (money/token spend) — you approved this.")
    else:
        print("  KEPT: SOUL.md and usage.jsonl (your real spend — pass --reset-spend to wipe).")
    print("  Run `waku dashboard` and start filming.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reset .waku to a clean demo state.")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="required confirmation: yes, wipe .waku (it is backed up first)")
    parser.add_argument("--reset-spend", action="store_true",
                        help="also wipe usage.jsonl (the money/token spend ledger)")
    args = parser.parse_args()
    if not args.yes:
        # 安全门：这会销毁真实的记忆/日历/追踪。除非用户用 --yes 明确确认，
        # 否则拒绝执行。参见 CLAUDE.md（“未经事先询问绝不清理运行时数据”）。
        # 它会备份，但恢复很麻烦。
        print("REFUSING to run: demo_seed clears .waku (memory, calendar, chat, traces"
              + (", AND spend" if args.reset_spend else "") + ").")
        print("This is destructive. If you truly mean it, re-run with --yes:")
        print("    python scripts/demo_seed.py --yes"
              + (" --reset-spend" if args.reset_spend else ""))
        raise SystemExit(2)
    main(reset_spend=args.reset_spend)
