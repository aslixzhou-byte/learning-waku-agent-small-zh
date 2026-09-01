"""一个微型的、自包含的 MCP 服务器，waku-agent 的演示连接器。"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo")

@mcp.tool()
def word_count(text: str) -> str:
    """统计一段文本中的单词数和字符数。"""
    return f"{len(text.split())} words, {len(text)} characters"


@mcp.tool()
def reverse_text(text: str) -> str:
    """反转字符串（便于证明连接器能来回传数据）。"""
    return text[::-1]

if __name__ == "__main__":
    mcp.run()  # stdio 传输—Waku 的 MCPBridge 正是通过它与它通信
