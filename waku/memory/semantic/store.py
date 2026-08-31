"""语义记忆 —— 持久事实，用 SQLite FTS5 做关键词检索。

白板上来自 Hermes 的洞见："关键词 top-k，不要 embedding"。对单个用户的事实来说，
带排名的关键词检索（BM25）快速、完全本地化，而且 —— 对教学尤为关键 ——
你可以用 sqlite3 直接读取整个索引。想要向量？设置 WAKU_SEMANTIC_STORE=supabase
（见 supabase_store.py）。
"""

from __future__ import annotations  # 让类型注解（如 sqlite3.Connection）在旧版 Python 里也能用

import re  # 正则：把用户文本拆成 FTS 的合法 token
import sqlite3  # SQLite：facts 表 + FTS5 索引（state.db）


def _fts_query(text: str) -> str:
    """用户的文本不是合法的 FTS5 查询（引号/标点会破坏 MATCH）。
    把它化简为基于字母数字 token 的 `word OR word OR ...`。"""
    words = re.findall(r"[a-zA-Z0-9]{2,}", text.lower())  # 只保留 ≥2 位的字母数字 token（过滤标点/单字母）
    return " OR ".join(dict.fromkeys(words)) if words else ""  # 去重后用 OR 连成合法查询；空文本返回空串
    # dict.fromkeys 去重并保持首次出现顺序，避免重复 token 让 MATCH 报错


class SqliteFactStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn  # 共享的 SQLite 连接（facts 表 + facts_fts 索引）

    def add(self, subject: str, content: str, source: str = "user") -> None:
        self.conn.execute(
            "INSERT INTO facts (subject, content, source) VALUES (?,?,?)",
            (subject.lower().strip(), content, source),  # 主题归一化：转小写并去掉首尾空格，便于后续精确匹配
        )
        self.conn.commit()  # 立即落库（facts_au 触发器会同步更新 FTS 索引）

    def search(self, query: str, top_k: int = 4) -> list[str]:
        fts = _fts_query(query)  # 先把用户文本转成合法 FTS5 查询
        if not fts:
            return []  # 空查询直接返回空，不触发 MATCH 报错
        rows = self.conn.execute(  # FTS 索引命中后 JOIN 回原表取正文
            "SELECT f.subject, f.content FROM facts_fts JOIN facts f ON f.id = facts_fts.rowid "
            "WHERE facts_fts MATCH ? ORDER BY rank LIMIT ?",  # rank 是 BM25 相关度，越接近 0 越相关
            (fts, top_k),
        ).fetchall()
        return [f"[{r['subject']}] {r['content']}" for r in rows]  # 拼成 "[主题] 内容" 文本，供注入提示词

    # --- CRUD：人类（dashboard）和代理（manage_memory 工具）都会编辑记忆。
    # facts_au / facts_ad 触发器会自动保持 FTS 索引同步。
    def list(self, limit: int = 200) -> list[dict]:
        rows = self.conn.execute(  # 给 dashboard 的完整事实列表，最新的在前
            "SELECT id, subject, content, source, created_at FROM facts ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]  # sqlite3.Row 直接转 dict

    def search_with_ids(self, query: str, top_k: int = 8) -> list[dict]:
        fts = _fts_query(query)
        if not fts:
            return self.list(top_k)  # 空查询回退为列出前 top_k 条
        rows = self.conn.execute(  # 与 search 相同，但额外带回 id，供上层更新/删除时定位
            "SELECT f.id, f.subject, f.content FROM facts_fts JOIN facts f ON f.id = facts_fts.rowid "
            "WHERE facts_fts MATCH ? ORDER BY rank LIMIT ?",
            (fts, top_k),
        ).fetchall()
        return [dict(r) for r in rows]

    def update(self, fact_id: int, content: str, subject: str | None = None) -> bool:
        if subject is None:
            cur = self.conn.execute("UPDATE facts SET content=? WHERE id=?", (content, fact_id))
            # 只改内容，主题保持不变
        else:
            cur = self.conn.execute(  # 内容 + 主题一起改（主题同样归一化）
                "UPDATE facts SET content=?, subject=? WHERE id=?",
                (content, subject.lower().strip(), fact_id),
            )
        self.conn.commit()
        return cur.rowcount > 0  # 影响行数 > 0 说明目标行存在、更新成功

    def delete(self, fact_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM facts WHERE id=?", (fact_id,))
        self.conn.commit()
        return cur.rowcount > 0  # 同上：真的删掉了才返回 True
