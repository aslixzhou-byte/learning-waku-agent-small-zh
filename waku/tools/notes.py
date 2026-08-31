"""save_note —— 应请求把一条持久事实写入语义记忆。

这是*显式*记忆路径（"记住 Alex 更喜欢上午"）。
*隐式*路径是记忆整合（waku/memory/consolidation.py），
它无需被请求，就从聊天历史中提炼出事实。
"""

from __future__ import annotations  # 让类型注解在旧版 Python 里也能用

import sqlite3  # 写入语义记忆数据库

from waku.tools.registry import Tool  # 工具定义类


def make_tool(conn: sqlite3.Connection) -> Tool:
    def save_note(subject: str, content: str) -> str:
        conn.execute(  # 插入一条事实；来源标 'user'（区别于记忆整合自动提炼的）
            "INSERT INTO facts (subject, content, source) VALUES (?,?,'user')",
            (subject.lower().strip(), content),  # 主题归一化：小写去空白，检索时更易精确命中
        )
        conn.commit()  # 提交事务
        return f"Saved to memory under '{subject}': {content}"  # 确认保存并转述给用户

    return Tool(
        name="save_note",
        description=(
            # 提示词：把一条持久事实保存到长期记忆。当用户告诉你关于他们自己、
            # 某个人或某个项目值得记住的事时使用——尤其是当他们说"记住"或
            # 分享一个偏好时。
            "Save a durable fact to long-term memory. Use when the user tells you something "
            "worth remembering about themselves, a person, or a project — especially if they "
            "say 'remember' or share a preference."
        ),
        input_schema={
            "type": "object",
            "properties": {
                # 提示词：这关于谁/什么，例如 'alex' 或 'acme-project'
                "subject": {"type": "string", "description": "Who/what this is about, e.g. 'alex' or 'acme-project'"},
                # 提示词：这条事实，一句话
                "content": {"type": "string", "description": "The fact, one sentence"},
            },
            "required": ["subject", "content"],
        },
        fn=save_note,
    )
