"""让代理管理自己记忆的工具

三个工具：
  manage_memory  —— 搜索 / 更新 / 删除事实与情景（CRUD）
  update_soul    —— 向 SOUL.md 追加一条持久的行事规则（其人设）
  create_skill   —— 编写新的 SKILL.md，让代理构建自己的程序性记忆

所有内容都写入仪表板展示的那些本地文件；不会离开这台机器。
update_soul 是只追加的（代理不能删除自己的诚实规则）；
完整改写由人在仪表板里完成。
"""

from __future__ import annotations  # 让类型注解在旧版 Python 里也能用

import re  # 校验技能名 slug 格式

from waku.memory import REPO_SKILLS  # 仓库内置技能的根目录（避免覆盖它们）
from waku.memory.procedural.loader import _parse_text  # 复用 SKILL.md 解析器做写入前校验
from waku.tools.registry import Tool  # 工具定义类

SOUL_MAX = 8000  # SOUL.md 长度上限（字符）：防止人设无限膨胀
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")  # 技能名格式：小写字母/数字开头，含连字符，1–41 位


def make_manage_memory_tool(memory) -> Tool:
    facts = memory.facts        # 语义事实存储器
    episodes = memory.episodes  # 情景记忆存储器

    def manage_memory(action: str, kind: str = "fact", id: int = 0,
                      query: str = "", content: str = "", subject: str = "") -> str:
        action = (action or "").lower()  # 动作归一化，避免模型传 "SEARCH" 之类大写
        if action == "search":
            if kind == "episode":  # 搜情景：只取最近 20 条，可选按摘要过滤
                rows = episodes.list(20)
                if query:
                    rows = [r for r in rows if query.lower() in r["summary"].lower()]  # 大小写不敏感的包含匹配
                return "\n".join(f"#{r['id']} ({r['happened_at']}) {r['summary']}" for r in rows[:8]) or "no episodes"  # 最多给 8 条
            # 搜事实：有 search_with_ids 就用它（拿 id 供后续 update/delete）
            rows = facts.search_with_ids(query, 8) if hasattr(facts, "search_with_ids") else []
            return "\n".join(f"#{r['id']} [{r['subject']}] {r['content']}" for r in rows) or "no matching facts"
        if action == "update":
            if kind != "fact":  # 情景是历史记录，不允许改写
                return "Only facts can be updated (episodes are historical)."
            ok = facts.update(int(id), content, subject or None)  # 按 id 更新事实；subject 可选
            return f"Updated fact #{id}." if ok else f"No fact with id {id}."  # 成败如实回报
        if action == "delete":
            if kind == "episode":
                # sqlite 的 id 是整数；notion 的页面 id 是 UUID 字符串——按形状强制转换。
                rid = int(id) if str(id).isdigit() else str(id)
                return f"Deleted episode #{id}." if episodes.delete(rid) else f"No episode with id {id}."
            return f"Deleted fact #{id}." if facts.delete(int(id)) else f"No fact with id {id}."
        return "action must be one of: search, update, delete"  # 未知动作：给出合法值列表

    return Tool(
        name="manage_memory",
        description=(
            # 提示词：搜索、纠正或删除用户的长期记忆（事实和情景）。
            # 一定要先搜索拿到 id，再更新或删除那个 id。
            # 当用户说你记错的某件事、或某件应该被遗忘的事时使用。
            "Search, correct, or delete the user's long-term memory (facts and episodes). "
            "ALWAYS search first to get the id, then update or delete that id. "
            "Use when the user says something you remember is wrong or should be forgotten."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["search", "update", "delete"]},  # 只允许这三个动作
                # 提示词：默认 fact
                "kind": {"type": "string", "enum": ["fact", "episode"], "description": "default fact"},
                # 提示词：行 id（来自之前的搜索）；sqlite 用数字，启用 notion 后端时
                # 用页面 id 字符串
                "id": {"type": ["integer", "string"],  # 两种类型都可能出现
                       "description": "row id (from a prior search); a number for sqlite, a page id string when the notion backend is active"},
                # 提示词：搜索用的关键词
                "query": {"type": "string", "description": "keywords for search"},
                # 提示词：更新用的新文本
                "content": {"type": "string", "description": "new text for update"},
                # 提示词：事实更新的可选新主题
                "subject": {"type": "string", "description": "optional new subject for a fact update"},
            },
            "required": ["action"],
        },
        fn=manage_memory,
    )


def make_update_soul_tool(settings) -> Tool:
    from waku.runtime.session import load_soul  # 局部导入：读取现有 SOUL.md 文本

    def update_soul(rule: str) -> str:
        rule = rule.strip().lstrip("-").strip()  # 去空白 + 去掉模型可能多加的列表符号
        if not rule:  # 空规则没意义
            return "Nothing to add."
        path = settings.home / "SOUL.md"  # 人设文件路径
        text = load_soul(settings)  # 确保文件存在
        if len(text) > SOUL_MAX:  # 超上限：拒绝追加，引导去仪表板手动编辑
            return "SOUL.md is at its size limit — edit it in the dashboard instead."
        if "## Learned rules" not in text:  # 还没有"习得规则"小节就补建一个
            text = text.rstrip() + "\n\n## Learned rules\n"
        text = text.rstrip() + f"\n- {rule}\n"  # 只追加（代理不能删除已有规则）
        path.write_text(text)
        return f"Noted, I'll remember to: {rule}"  # 确认已记住，转述给用户

    return Tool(
        name="update_soul",
        description=(
            # 提示词：保存一条关于你该如何为这位用户行事的持久规则（他们的
            # 偏好和长期指示）。追加到你的人设；下一轮生效。
            # 当用户告诉你他们希望你如何表现时使用。
            "Save a durable rule about how you should behave for this user (their "
            "preferences and standing instructions). Appends to your persona; takes "
            "effect next turn. Use when the user tells you how they want you to act."
        ),
        input_schema={
            "type": "object",
            "properties": {
                # 提示词：一条行为规则，用祈使句
                "rule": {"type": "string", "description": "one behaviour rule, imperative"}},
            "required": ["rule"],
        },
        fn=update_soul,
    )


def make_create_skill_tool(settings, memory) -> Tool:
    def create_skill(name: str, description: str, body: str) -> str:
        name = (name or "").strip().lower().replace(" ", "-")  # 归一化：小写、空格换连字符
        if not _SLUG.match(name):  # 格式不对：给个合法示例
            return "Skill name must be a short slug like 'weekly-review' (lowercase, hyphens)."
        dest = settings.home / "skills" / name / "SKILL.md"  # 用户技能目录
        # 绝不静默覆盖已有技能（内置的或用户的）
        if dest.exists() or (REPO_SKILLS / name / "SKILL.md").exists():
            return f"A skill named '{name}' already exists — pick another name."
        text = f"---\nname: {name}\ndescription: {description.strip()}\n---\n\n{body.strip()}\n"  # 拼出 SKILL.md 前导元数据 + 正文
        if _parse_text(text, dest) is None:  # 用解析器先验证；写坏的文件宁可拒绝
            return "That didn't validate — description must be present and non-trivial."
        dest.parent.mkdir(parents=True, exist_ok=True)  # 确保技能目录存在
        dest.write_text(text, encoding="utf-8")  # 落盘
        memory.skills.refresh()  # 本会话内立即生效
        return f"Created skill '{name}'. It will trigger on: {description.strip()}"  # 回报触发条件

    return Tool(
        name="create_skill",
        description=(
            # 提示词：编写一个新的可复用技能（一份代理在相关时加载的 SKILL.md），
            # 以便你能重复用户教你的某个工作流。只有在用户同意后才能调用。
            # body = 逐步指令；description = 何时使用（要包含触发词）。
            "Write a new reusable skill (a SKILL.md the agent loads when relevant) so you "
            "can repeat a workflow the user taught you. Only call this after the user agrees. "
            "body = step-by-step instructions; description = when to use it (include trigger words)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                # 提示词：短 slug，例如 weekly-review
                "name": {"type": "string", "description": "short slug, e.g. weekly-review"},
                # 提示词：一行：它做什么、何时使用
                "description": {"type": "string", "description": "one line: what it does and when to use it"},
                # 提示词：逐步指令（markdown）
                "body": {"type": "string", "description": "the step-by-step instructions (markdown)"},
            },
            "required": ["name", "description", "body"],
        },
        fn=create_skill,
    )
