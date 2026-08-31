"""一个 SQLite 文件（state.db）装下 Waku 记住和做过的所有事。

这对应白板上的 Hermes 方案：SQLite + FTS5，无需服务器。
随时可自己打开：  sqlite3 .waku/state.db '.tables'
"""

from __future__ import annotations  # 让类型注解在旧版 Python 里也能用

import sqlite3              # 标准库 SQLite：零依赖的本地存储
from pathlib import Path    # 跨平台路径（state.db 的位置）

SCHEMA = """
-- 旗舰任务产物：日历工具创建的事件。确定性评估直接断言这张表里的行
--（“会议到底触发了没有？”）。
CREATE TABLE IF NOT EXISTS calendar_events (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    start TEXT NOT NULL,           -- ISO 8601
    "end" TEXT,
    attendees TEXT DEFAULT '',     -- 逗号分隔
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);

-- 语义记忆：关于你、你的人、你的项目的持久事实。
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY,
    subject TEXT NOT NULL,         -- 事实所指的人/物，如 'alex'
    content TEXT NOT NULL,         -- 事实本身
    source TEXT DEFAULT 'user',    -- 'user'（直接告知）或 'consolidation'（整合得出）
    created_at TEXT DEFAULT (datetime('now'))
);
-- FTS5 全文索引表：外挂的虚表，内容指向 facts 主表（content=facts、content_rowid=id 两行声明）
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    subject, content, content=facts, content_rowid=id
);
-- 触发器：facts 新增一行时，同步把它的 subject/content 写进全文索引（保证可检索）
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, subject, content) VALUES (new.id, new.subject, new.content);
END;
-- 触发器：facts 删除一行时，从全文索引删掉对应行（'delete' 是 fts5 的特殊删除标记）
CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, subject, content) VALUES ('delete', old.id, old.subject, old.content);
END;
-- 触发器：facts 更新一行时，先删旧索引再插新索引（保证索引与主表永远一致）
CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, subject, content) VALUES ('delete', old.id, old.subject, old.content);
    INSERT INTO facts_fts(rowid, subject, content) VALUES (new.id, new.subject, new.content);
END;

-- 情景记忆：发生过的事情（过去的对话，已提炼）。
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY,
    happened_at TEXT NOT NULL,     -- 情景的 ISO 8601 日期
    summary TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
-- FTS5 全文索引：只索引 episodes 的 summary 列（情景检索按摘要全文匹配）
CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
    summary, content=episodes, content_rowid=id
);
-- 触发器：episodes 新增时同步索引摘要
CREATE TRIGGER IF NOT EXISTS episodes_ai AFTER INSERT ON episodes BEGIN
    INSERT INTO episodes_fts(rowid, summary) VALUES (new.id, new.summary);
END;
-- 触发器：episodes 删除时同步清理索引
CREATE TRIGGER IF NOT EXISTS episodes_ad AFTER DELETE ON episodes BEGIN
    INSERT INTO episodes_fts(episodes_fts, rowid, summary) VALUES ('delete', old.id, old.summary);
END;

-- 原始聊天日志（“保存消息”的盒子）。整合从这里读取。
-- session_id 标记每行属于哪段对话，这样 dashboard 能提供「新建聊天」并在
-- 历史会话间切换（就像聊天应用）。所有内容共用这一张表 —— 会话只是个标签。
CREATE TABLE IF NOT EXISTS chat_log (
    id INTEGER PRIMARY KEY,
    role TEXT NOT NULL,            -- 'user' | 'assistant'
    content TEXT NOT NULL,
    consolidated INTEGER DEFAULT 0,
    session_id TEXT DEFAULT 'default',
    created_at TEXT DEFAULT (datetime('now'))
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """对「在某个列存在之前创建」的数据库做增量的、幂等的列升级。
    SQLite 没有 'ADD COLUMN IF NOT EXISTS'，所以我们要先检查。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(chat_log)").fetchall()}  # 读出 chat_log 现有列名集合
    if "session_id" not in cols:  # 老库没有会话列：补上（默认 'default' 兼容旧数据）
        conn.execute("ALTER TABLE chat_log ADD COLUMN session_id TEXT DEFAULT 'default'")
        conn.commit()
    if "source" not in cols:
        # 一条消息是从哪个网关进来的（cli / voice / telegram / dashboard）
        conn.execute("ALTER TABLE chat_log ADD COLUMN source TEXT DEFAULT 'cli'")
        conn.commit()
    if "meta" not in cols:
        # 每轮的遥测，以 JSON 存在 assistant 行上（门禁决策、延迟、迭代次数、工具）
        # —— 这样重新打开一个线程时仍能看到每条回答是如何产生的，而不只是纯文本。
        conn.execute("ALTER TABLE chat_log ADD COLUMN meta TEXT")
        conn.commit()


def connect(home: Path, check_same_thread: bool = True) -> sqlite3.Connection:
    # check_same_thread=False 让 dashboard 的多线程 HTTP 服务器能在多个工作线程间
    # 复用一个 agent 连接（由一把锁保护）。busy_timeout 避免在聊天写入时
    # dashboard 读取出现「database is locked」。
    conn = sqlite3.connect(home / "state.db", check_same_thread=check_same_thread)  # 打开/创建 state.db
    conn.row_factory = sqlite3.Row  # 行按字典式访问（row["title"]），而不是元组下标
    conn.execute("PRAGMA busy_timeout=3000")  # 写锁冲突时最多等 3 秒，避免立刻报 locked
    conn.executescript(SCHEMA)  # 一次性执行全部建表语句（幂等，可重复跑）
    _migrate(conn)  # 给老库补上缺失的列
    return conn
