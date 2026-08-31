"""情景记忆 —— 带日期的事件：发生了什么，以及何时发生。

语义记忆回答"我知道什么？"；情景记忆回答"上周二发生了什么？"。
用的是同一个 SQLite 文件，但每一行都带日期，检索把相关性（FTS 排名）与
时效性结合起来 —— 即白板上的"相关性用 RAG + 时效性用 SQL"。
"""

from __future__ import annotations  # 让类型注解（如 sqlite3.Connection）在旧版 Python 里也能用

import sqlite3  # SQLite：与语义记忆共用同一个连接（state.db）

from waku.memory.semantic.store import _fts_query  # 复用语义记忆的文本→FTS5 查询转换器


class SqliteEpisodeStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn  # 共享的 SQLite 连接（episodes 表 + episodes_fts 索引）

    def add(self, summary: str, happened_at: str) -> None:
        self.conn.execute(  # 插入一条带日期的情景摘要
            "INSERT INTO episodes (happened_at, summary) VALUES (?,?)",
            (happened_at, summary),
        )
        self.conn.commit()  # 立即落库

    def search(self, query: str, top_k: int = 3) -> list[str]:
        """相关性优先（FTS），匹配结果中最新在前。"""
        fts = _fts_query(query)  # 把用户文本转成合法 FTS5 查询
        if not fts:
            return self.recent(top_k)  # 空查询回退到最近事件，而非报错
        rows = self.conn.execute(  # 在 FTS 索引里搜，命中后 JOIN 回原表取日期与正文
            "SELECT e.happened_at, e.summary FROM episodes_fts JOIN episodes e "
            "ON e.id = episodes_fts.rowid WHERE episodes_fts MATCH ? "
            "ORDER BY rank, e.happened_at DESC LIMIT ?",  # rank 相关度优先，同分按时间新在前
            (fts, top_k),
        ).fetchall()
        return [f"({r['happened_at']}) {r['summary']}" for r in rows]  # 与语义 store 相同的 "(日期) 摘要" 格式

    def recent(self, top_k: int = 3) -> list[str]:
        rows = self.conn.execute(  # 纯按时间取最新 N 条（不涉及检索）
            "SELECT happened_at, summary FROM episodes ORDER BY happened_at DESC LIMIT ?",
            (top_k,),
        ).fetchall()
        return [f"({r['happened_at']}) {r['summary']}" for r in rows]

    def list(self, limit: int = 200) -> list[dict]:
        rows = self.conn.execute(  # 给 dashboard 的完整事件列表，新的在前
            "SELECT id, happened_at, summary, created_at FROM episodes ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]  # sqlite3.Row 直接转 dict

    def delete(self, episode_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM episodes WHERE id=?", (episode_id,))
        self.conn.commit()
        return cur.rowcount > 0  # 影响行数 > 0 说明真的删掉了
