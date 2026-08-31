"""记忆门面 —— 三大支柱背后的小接口。

    procedural  SKILL.md 文件      如何行动
    semantic    facts 表 (FTS5)   什么是持久的真相
    episodic    episodes 表       发生了什么、何时发生

外加两个管理它们的代理：
    retrieval_gate   决定某轮是否需要记忆   (高光时刻 #1)
    consolidation    每 N 次交互把对话蒸馏成事实
"""

from __future__ import annotations  # 让类型注解（如 sqlite3.Connection）在旧版 Python 里也能用

import sqlite3  # SQLite：三大记忆共用的本地文件（state.db）
from pathlib import Path  # 跨平台的文件路径操作

import anthropic  # Anthropic SDK：门禁/整合都要调用的小模型

from waku.config import Settings  # 配置对象：store 开关、模型名、top_k 等
from waku.memory import consolidation, retrieval_gate  # 引入两个管理代理：整合 + 门禁
from waku.memory.episodic.store import SqliteEpisodeStore  # 情景记忆的默认 SQLite 实现
from waku.memory.procedural.loader import SkillLoader  # 程序性记忆：扫描 SKILL.md 的加载器
from waku.memory.semantic.store import SqliteFactStore  # 语义记忆的默认 SQLite 实现

# 仓库自带的 skills/ 目录（内置 + 社区技能），与用户自己安装的 skills 目录并列扫描
REPO_SKILLS = Path(__file__).resolve().parents[2] / "skills"


class Memory:
    def __init__(self, conn: sqlite3.Connection, settings: Settings, client: anthropic.Anthropic,
                 episode_store=None):
        # episode_store：注入一个已构建好的存储（dashboard 在进程级缓存了唯一一个
        # NotionEpisodeStore —— 其构造函数会访问网络，所以每个 Memory 各建一个
        # 会导致每次轮询都重新查询 Notion）。
        self.conn = conn  # 共享的 SQLite 连接（facts/episodes/chat_log 都在这里）
        self.settings = settings  # 配置：store 选择、小模型名、top_k 等
        self.client = client  # Anthropic 客户端：门禁/整合都要用
        self.facts = self._make_fact_store(conn, settings)  # 语义记忆（默认 FTS5，可切换 Supabase 向量）
        self.episodes = episode_store if episode_store is not None else self._make_episode_store(conn, settings)
        # 情景记忆：优先用注入实例（避免重复访问 Notion），否则按配置新建
        self.skills = SkillLoader([REPO_SKILLS, settings.home / "skills"])  # 程序性记忆：两个 skill 目录一起扫描

    @staticmethod
    def _make_fact_store(conn, settings):
        if settings.semantic_store == "supabase":  # 配置切换为向量检索时
            from waku.memory.semantic.supabase_store import SupabaseFactStore  # 延迟导入：用到才拉依赖（extra）

            return SupabaseFactStore(settings)  # 向量版：Supabase pgvector + embedding
        return SqliteFactStore(conn)  # 默认：SQLite FTS5 关键词检索（本地、可直接读）

    @staticmethod
    def _make_episode_store(conn, settings):
        if settings.episodic_store == "notion":  # 配置切换为云端 Notion 时
            from waku.memory.episodic.notion_store import NotionEpisodeStore  # 同样延迟导入（依赖在 [notion] extra 里）

            return NotionEpisodeStore()  # 云端版：每个 episode 是 Notion 的一页
        return SqliteEpisodeStore(conn)  # 默认：SQLite 带日期的 episode 表

    # ---- 检索（带门禁 —— 原因见 retrieval_gate.py）
    def gated_retrieve(self, message: str, notify=None) -> str:
        # 先让便宜的小模型决定：这条消息需要记忆吗？（避免无关记忆干扰答案）
        retrieve, query, reason = retrieval_gate.should_retrieve(
            self.client, self.settings.small_model, message
        )
        if notify:  # dashboard 等观察者想看到门禁的裁决
            notify("gate", {"decision": "retrieve" if retrieve else "skip", "reason": reason})
        if not retrieve:
            return ""  # 门禁说不检索：直接返回空，不触碰任何存储
        found = self.facts.search(query, self.settings.retrieval_top_k)  # 语义记忆：FTS5 关键词 top-k
        found += self.episodes.search(query, top_k=3)  # 情景记忆：相关事件（固定取 3 条）
        return "\n".join(found)  # 拼成一段文本，供后续注入提示词

    # ---- 程序性记忆
    def matching_skills(self, message: str) -> str:
        matched = self.skills.match(message)  # 关键词重叠匹配，只挑相关的 skill
        return "\n\n".join(f"### {s.name}\n{s.body}" for s in matched)  # 以 markdown 标题 + 正文拼进提示词

    # ---- 写入路径
    def log_chat(self, user_message: str, reply: str, session_id: str = "default",
                 source: str = "cli", meta: dict | None = None) -> None:
        import json as _json  # 局部导入：只有需要序列化 meta 时才用到
        self.conn.execute(
            "INSERT INTO chat_log (role, content, session_id, source) VALUES ('user', ?, ?, ?)",
            (user_message, session_id, source),
        )
        # meta（门禁/延迟/迭代次数/工具）挂载在 assistant 行上，
        # 这样重新打开的会话也能渲染出完整的一轮卡片，而不只是文本。
        self.conn.execute(
            "INSERT INTO chat_log (role, content, session_id, source, meta) VALUES ('assistant', ?, ?, ?, ?)",
            (reply, session_id, source, _json.dumps(meta) if meta else None),  # 有 meta 才序列化，否则存 NULL
        )
        self.conn.commit()  # 两条一起落库：user + assistant 才算完整一轮

    # ---- 会话（供 dashboard 的聊天历史 + "New chat" 使用）
    def session_history(self, session_id: str) -> list[tuple[str, str]]:
        """某个历史会话的 (user, assistant) 交互序列，按顺序排列 —— 当用户切回
        某个对话时，用它来重新加载工作记忆。"""
        rows = self.conn.execute(
            "SELECT role, content FROM chat_log WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        pairs, pending = [], None  # pending 暂存当前 user 消息，遇到 assistant 再配对
        for r in rows:
            if r["role"] == "user":
                pending = r["content"]  # 记下最新的 user 消息
            elif pending is not None:  # 没有 user 打头的一行（脏数据）就跳过
                pairs.append((pending, r["content"]))
                pending = None  # 配对完成，等待下一条 user
        return pairs

    def list_sessions(self) -> list[dict]:
        """每个对话一行：id、第一条用户消息（即标题）、消息数量，以及开始时间 ——
        最新在前。"""
        rows = self.conn.execute(
            """SELECT session_id,
                      COUNT(*) AS messages,
                      MIN(created_at) AS started_at,
                      MAX(created_at) AS last_at
               FROM chat_log GROUP BY session_id ORDER BY last_at DESC"""  # 按会话分组，最近活跃的排最前
        ).fetchall()
        out = []
        for r in rows:
            first = self.conn.execute(  # 每条会话取第一条 user 消息当标题
                "SELECT content FROM chat_log WHERE session_id = ? AND role = 'user' ORDER BY id LIMIT 1",
                (r["session_id"],),
            ).fetchone()
            out.append({
                "id": r["session_id"],
                "title": (first["content"][:60] if first else "(empty)"),  # 标题截断到 60 字符；空会话显示占位
                "messages": r["messages"],
                "started_at": r["started_at"],
                "last_at": r["last_at"],
            })
        return out

    def export_markdown(self) -> None:
        """把记忆镜像到 state.db 旁边的人类可读 MEMORY.md —— 这样白板上的
        `~/.waku/MEMORY.md` 盒子就真实存在，"你的记忆是一个能打开的文件" 这句话
        也成真。state.db 仍是可查询的事实源；此文件是每次轮询后重新生成的视图。"""
        facts = self.conn.execute(  # 语义记忆：事实列表（按主题聚合成组，便于人读）
            "SELECT subject, content FROM facts ORDER BY subject, id"
        ).fetchall()
        eps = self.conn.execute(  # 情景记忆：事件列表（最新排最前）
            "SELECT happened_at, summary FROM episodes ORDER BY happened_at DESC, id DESC"
        ).fetchall()
        lines = [  # 组装 markdown：标题 + 镜像说明 + 各小节
            "# Waku memory",
            "",
            "_A human-readable mirror of what Waku remembers. The source of truth is "
            "`state.db` (the `facts` and `episodes` tables, keyword-searchable via FTS5); "
            "this file is regenerated after every turn._",
            "",
            f"## Facts — semantic memory ({len(facts)})",
            "",
        ]
        lines += [f"- **{f['subject']}** — {f['content']}" for f in facts] or ["_none yet_"]
        # 上面的 or：空表时兜底显示 _none yet_，而不是留下一个空小节
        lines += ["", f"## Episodes — episodic memory ({len(eps)})", ""]
        lines += [f"- **{e['happened_at']}** — {e['summary']}" for e in eps] or ["_none yet_"]
        (self.settings.home / "MEMORY.md").write_text("\n".join(lines) + "\n")  # 覆盖写回用户目录，替换上次的镜像

    def maybe_consolidate(self, notify=None) -> None:
        # 到期（距上次又攒够 N 次交互）才蒸馏，否则 consolidate_if_due 直接返回 0
        new_facts = consolidation.consolidate_if_due(
            self.conn,
            self.client,
            self.settings.small_model,
            self.settings.consolidate_every,
            self.facts,
            self.episodes,
        )
        if new_facts and notify:  # 真有新事实时通知观察者（dashboard 据此展示"记忆已更新"）
            notify("consolidation", {"new_facts": new_facts})
