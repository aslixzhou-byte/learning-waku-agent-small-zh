"""一个微型的、自包含的 MCP 服务器——waku-agent 的演示连接器。

大多数 MCP 示例需要 Node/npx。这个只用纯 Python（仅需 `mcp` 附加依赖），
因此连接器的使用说明无需任何额外安装即可跑通：

    pip install -e '.[mcp]'
    cp examples/mcp.demo.json .waku/mcp.json
    make dashboard          # 它的工具出现在 Tools > Available > MCP servers 下

它的工具注册为 `demo_word_count` 和 `demo_reverse_text`。换成你自己的
@mcp.tool() 函数，或者用同样方式把 mcp.json 指向任意真实 MCP 服务器——
这正是重点：连接器即插即用，无需改动 Waku 的代码。
"""

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
    mcp.run()  # stdio 传输——Waku 的 MCPBridge 正是通过它与它通信
