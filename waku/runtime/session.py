"""短暂的一次 Agent 运行 —— 为每一轮组装工作记忆。

白板上的内层盒子：这里的一切每次运行都重建并丢弃。持久化的东西在 waku/memory。工作记忆 =

    system prompt (SOUL.md)            ← Waku 是谁（人设）
  + durable facts & episodes           ← Waku 记得什么（经门禁把关！）
  + current chat history               ← 本次对话
  + the user's new message
"""

from __future__ import annotations  # 让类型注解在旧版 Python 里也能用

from waku.config import Settings  # 配置：决定 home 路径、历史窗口等

# 默认人设（系统提示词）。以下逐段给出中文对照说明 —— 提示词本身保持英文，
# 因为它是直接发给 LLM 的，改成中文会改变模型行为。
#   第 1 段：你是 Waku，一个跑在用户笔记本上的本地个人助理。你简洁、温暖、主动，记得用户告诉过你的事。
#   第 2 段：用户要安排日程时用 create_event，自行把相对时间（“下周二”“30 分钟后”）换算成 ISO 时间戳；
#             当前日期时间已在下方给出 —— 相信它们，永远不要反问用户现在几点。
#   第 3 段：用户问日历（某天/一周/“昨天”）时用 list_events —— 你能读日历，不只是写。
#   第 4 段：用户分享关于某个人/项目/偏好的持久信息时，用 save_note 记下来。
#   第 5 段：用户要给别人发消息时，用 send_message（它会草拟到本地发件箱）。
#   第 6 段：如果下方提供了记忆上下文，相信它 —— 它来自你自己的记忆库。
#   第 7 段：每次请求每个工具最多调用一次。历史里显示了过往轮次的 [tools used: ...] 行
#             —— 如果某个工具已经跑过，不要再跑，直接根据那条记录回答。
#   第 8 段：如实说明东西存在哪里。每个工具的输出都明确写明了产物落在哪
#             （本地日历文件、Apple Calendar、.waku/state.db 记忆数据库）—— 如实转述，
#             绝不要声称同步到了工具输出没提到的任何地方。
#   第 9 段：你能管理自己的记忆：用 manage_memory 纠正或遗忘事实，用 update_soul 保存
#             用户给你的长期偏好，用 create_skill 保存用户教你的可复用流程（仅在对方同意后）。
DEFAULT_SOUL = """\
You are Waku, a personal assistant running locally on your user's laptop.
You are concise, warm, and proactive. You remember what your user tells you.

Rules:
- When the user wants to schedule something, use create_event. Resolve relative
  dates and times ("next Tuesday", "in 30 minutes") to ISO timestamps yourself;
  the current date and time are given below — trust them, never ask the user
  what time it is.
- When the user asks what's on their calendar (a day, a week, "yesterday"), use
  list_events — you CAN read the calendar, not just write to it.
- When the user shares something durable about a person, project, or preference,
  use save_note to remember it.
- When asked to message someone, use send_message (it drafts to a local outbox).
- If memory context is provided below, trust it — it came from your own store.
- Call each tool at most once per request. Your history shows [tools used: ...]
  lines for past turns — if a tool already ran, do NOT run it again; answer
  from that record instead.
- Be honest about where things live. Every tool's output states exactly where
  its artifact landed (local calendar file, Apple Calendar, memory database at
  .waku/state.db) — relay that truthfully, and never claim something synced
  anywhere the tool output doesn't say.
- You can manage your own memory: use manage_memory to correct or forget facts,
  update_soul to save a standing preference the user gives you, and create_skill
  to save a repeatable workflow the user teaches you (only after they say yes).
"""


def load_soul(settings: Settings) -> str:
    """SOUL.md 是可编辑的人设文件，首次运行时创建。改它就等于改你的 Waku 是谁
    —— 这就是最朴素的程序性记忆。"""
    soul_path = settings.home / "SOUL.md"  # 人设文件固定叫 SOUL.md，放在 home 目录下
    if not soul_path.exists():
        soul_path.write_text(DEFAULT_SOUL)  # 第一次跑：用内置模板建出可编辑的人设文件
    return soul_path.read_text()  # 每次运行都重读，保证改 SOUL.md 立即生效


class Session:
    """承载一段对话：聊天历史 + 系统提示词的组装配方。每个网关连接一个 Session。"""

    def __init__(self, settings: Settings, memory=None, session_id: str = "default"):
        self.settings = settings  # 配置（home、history_turns 等）
        self.memory = memory  # waku.memory.Memory（Phase-2 接线前为 None）
        self.session_id = session_id  # 当前会话标签（chat_log 行的分组键）
        self.history: list[dict] = []  # 本次会话的聊天历史（工作记忆的一部分），每次新建都清空

    def build_system(self, user_message: str, notify=None) -> str:
        """组装系统提示词：人设 + 当前时间 + 模型信息 +（记忆/技能上下文）。"""
        from datetime import datetime  # 局部导入：只在组装配方时才需要

        # agent 跑在你的笔记本上，所以它应当知道你笔记本的时钟。
        # 带时区名的本地时间 —— 足以解析「30 分钟后」这类表达。
        now = datetime.now().astimezone()  # 取带本地时区的当前时间
        parts = [load_soul(self.settings),  # 第 0 段：人设（SOUL.md，可编辑）
                 # 提示词：现在是「当前时间」，让 agent 解析相对日期/时间。
                 f"\nRight now it is {now:%A, %Y-%m-%d %H:%M} ({now:%Z}, UTC{now:%z}).",
                 # agent 应该知道自己的「大脑」——「你是什么模型？」是每个好奇用户的第一问。
                 # 提示词：告诉 agent 它运行在哪个模型/提供商上。
                 f"Your model: you are running on '{self.settings.model}' via the "
                 f"'{self.settings.provider}' provider, inside Waku, a local-first "
                 f"open-source agent harness (github.com/ShenSeanChen/waku-agent)."]

        if self.memory is not None:  # 还没接记忆时跳过检索（Phase-2 之前）
            # 高光时刻 #1：一个廉价的门禁判断到底「要不要检索」——
            # 默认开启的检索又慢又会带偏回答（原因见 memory/retrieval_gate.py）。
            retrieved = self.memory.gated_retrieve(user_message, notify=notify)  # 门禁放行才检索
            if retrieved:
                parts.append("\nRelevant memory:\n" + retrieved)  # 有相关记忆就拼进提示词，让 agent 信它
            skills = self.memory.matching_skills(user_message)  # 找匹配当前请求的流程技能
            if skills:
                parts.append("\nRelevant skill instructions:\n" + skills)  # 技能指令拼进提示词

        return "\n".join(parts)  # 各段用空行隔开，合成最终系统提示词

    def add_exchange(self, user_message: str, reply: str, tool_calls: list | None = None,
                     source: str = "cli", meta: dict | None = None) -> None:
        """把这一轮记录进历史（工作记忆），如果已接入记忆，也写进聊天日志
        （这样整合才能稍后提炼它）。

        工具活动会被折叠成 assistant 历史条目里的一行紧凑的 [tools used: ...]。
        没有它，模型会忘记自己已经行动过，下轮又开心地重跑同一个工具
        （就是首次实测里「同一会议被约三次」的 bug）。"""
        record = reply  # 默认记录就是回复原文
        if tool_calls:  # 这一轮动过工具：把工具活动折叠进回复
            summary = "; ".join(f"{c['tool']}({c['args']}) -> {c['output']}" for c in tool_calls)  # 每条调用一行「工具(参数) -> 结果」
            record = f"{reply}\n[tools used: {summary}]"  # 拼成一行紧凑记录，防模型下轮重跑同工具
        self.history.append({"role": "user", "content": user_message})  # 用户消息进工作记忆
        self.history.append({"role": "assistant", "content": record})   # 助手回复（含工具摘要）进工作记忆
        if self.memory is not None:
            self.memory.log_chat(user_message, record, session_id=self.session_id,  # 持久化到聊天日志，供整合提炼
                                 source=source, meta=meta)

    # ---- 会话生命周期（「新建聊天」/ 历史记录功能）
    # 会话只是 chat_log 行上的一个标签。新建会话清空工作记忆；切换则重新载入
    # 一段过往对话的历史，让回复有上下文。整合始终读取所有未整合的行，不受影响。
    def start_new(self, session_id: str) -> None:
        self.session_id = session_id  # 换成新会话的标签
        self.history = []  # 清空工作记忆：新会话从头开始，不带旧上下文

    def switch(self, session_id: str) -> None:
        self.session_id = session_id  # 切到目标会话
        self.history = []  # 先清空，下面重新载入该会话的历史
        if self.memory is None:
            return  # 没接记忆就没有可回放的日志，直接返回空历史
        # 只有过往对话的最近尾部会回到工作记忆
        # （respond() 也会做窗口裁剪，但别把整条线程都端着）。
        turns = self.settings.history_turns  # 只回放最近 N 轮，别把整条线程都端进来
        for user_msg, reply in list(self.memory.session_history(session_id))[-turns:]:  # 取该会话最近 N 轮（每轮两条）
            self.history.append({"role": "user", "content": user_msg})      # 逐条补回用户消息
            self.history.append({"role": "assistant", "content": reply})    # 逐条补回助手回复
