"""send_message 把一条消息草稿放进本地发件箱。
本地优先：实际上什么都不会发送。每条消息变成 .waku/outbox/ 里的一个文件
"""

from __future__ import annotations  # 让类型注解在旧版 Python 里也能用

from datetime import datetime, timezone  # UTC 时间戳：让文件名能按时间排序
from pathlib import Path                 # 发件箱目录的路径类型

from waku.tools.registry import Tool  # 工具定义类


def make_tool(home: Path) -> Tool:
    def send_message(to: str, body: str) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")  # UTC 时间戳：同名消息也能按时间排开
        safe_to = "".join(c if c.isalnum() else "-" for c in to)[:40]  # 收件人转成文件名安全字符，截到 40 位
        path = home / "outbox" / f"{stamp}-{safe_to}.txt"  # 草稿文件路径（只写本地，绝不外发）
        path.write_text(f"To: {to}\n\n{body}\n")
        return f"Message to {to} placed in outbox ({path.name}). Nothing was sent — review it there."  # 诚实说明没发出去

    return Tool(
        name="send_message",
        description=(
            # 提示词：给某人起草一条消息，放进本地发件箱供用户审阅并发送。
            # 当用户让你给某人发消息、转告或提醒时使用。
            "Draft a message to someone and place it in the local outbox for the user to "
            "review and send. Use when the user asks you to message, tell, or remind someone."
        ),
        input_schema={
            "type": "object",
            "properties": {
                # 提示词：收件人姓名或地址
                "to": {"type": "string", "description": "Recipient name or address"},
                # 提示词：消息正文
                "body": {"type": "string", "description": "The message text"},
            },
            "required": ["to", "body"],  # 两者都必填
        },
        fn=send_message,
    )
