"""search_web —— 第二个真正的工具，也是一个绝佳的循环演示。

"找出剩下的世界杯比赛并放到我的日历上"会让代理在多个工具间循环：
search_web（读取网页）→ 推理结果 → 每个匹配项创建一个事件。
在仪表板上观察循环盒子的运转。

零新增依赖——只用标准库 urllib。两个后端：
  默认    DuckDuckGo HTML（无需密钥，无需配置——足以演示）
  更佳    Tavily，如果设置了 TAVILY_API_KEY（或 WAKU_SEARCH_API_KEY）——
          一个对代理友好的搜索 API，结果更干净（免费额度）

该工具返回模型可读取的纯文本；它绝不为模型解析 HTML。
"""

from __future__ import annotations  # 让类型注解（list[tuple[str, str, str]]）在旧版 Python 里也能用

import html            # 反转义 HTML 实体
import json            # Tavily API 的请求/响应体
import os              # 读取搜索 API key 环境变量
import re              # 从 DDG 页面里抠出结果
import urllib.parse    # URL 编码查询词
import urllib.request  # 发起 HTTP 请求

from waku.tools.registry import Tool  # 工具定义类

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"  # 伪装浏览器 UA，降低被 DDG 拦截的概率


def _tavily(query: str, key: str, max_results: int) -> list[tuple[str, str, str]]:
    """Tavily 后端：结构化 API，返回 (标题, 摘要, URL) 元组列表。"""
    body = json.dumps({"api_key": key, "query": query, "max_results": max_results,
                       "include_answer": False}).encode()  # JSON 请求体；不额外要"答案"块
    req = urllib.request.Request("https://api.tavily.com/search", data=body,  # POST 到搜索端点
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:  # 20 秒超时，避免卡住工具调用
        data = json.loads(resp.read())
    return [(r.get("title", ""), (r.get("content", "") or "")[:400], r.get("url", ""))  # 摘要截到 400 字符
            for r in data.get("results", [])]


def _strip(text: str) -> str:
    """去掉 HTML 标签并反转义实体，得到干净的可读文本。"""
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _duckduckgo(query: str, max_results: int) -> list[tuple[str, str, str]]:
    """默认后端：抓 DDG 的 HTML 结果页并用正则解析（无需任何 key）。"""
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)  # 查询词 URL 编码
    req = urllib.request.Request(url, headers={"User-Agent": _UA})  # 带上浏览器 UA
    with urllib.request.urlopen(req, timeout=20) as resp:
        page = resp.read().decode("utf-8", "ignore")  # 解码失败就忽略坏字节
    links = re.findall(r'result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page, re.DOTALL)  # 抠出标题行
    snips = re.findall(r'result__snippet"[^>]*>(.*?)</a>', page, re.DOTALL)  # 抠出摘要行
    out = []
    for i, (href, title) in enumerate(links[:max_results]):  # 只取前 N 条
        target = href
        m = re.search(r"uddg=([^&]+)", href)  # DDG 把结果包在一个重定向里
        if m:  # 有重定向参数就解出真实 URL
            target = urllib.parse.unquote(m.group(1))
        out.append((_strip(title), _strip(snips[i]) if i < len(snips) else "", target))  # 清洗标题/摘要
    return out


def make_tool() -> Tool:
    def search_web(query: str, max_results: int = 5) -> str:
        key = os.getenv("TAVILY_API_KEY") or os.getenv("WAKU_SEARCH_API_KEY")  # 两个环境变量都认
        try:
            results = _tavily(query, key, max_results) if key else _duckduckgo(query, max_results)  # 有 key 走 Tavily，否则走 DDG
        except Exception as exc:
            results = None if key else []  # DDG 被拦截 → 落到下面的提示
            if key:  # 配了 key 还失败：如实告知并建议凭已有知识作答
                return f"Web search failed ({exc}). Answer from what you know, or ask the user."
        if not results:
            if not key:  # 没 key：解释免费端点的限制并引导配置 Tavily
                return ("No results — DuckDuckGo's free endpoint often blocks automated "
                        "requests. For reliable search set a free TAVILY_API_KEY in .env "
                        "(https://tavily.com); see .env.example. Meanwhile, tell the user "
                        "you couldn't search and ask them to add the key.")
            return "No results found. Try a more specific query."
        engine = "Tavily" if key else "DuckDuckGo"  # 报告实际用的引擎
        lines = [f"Web results for '{query}' (via {engine}):"]
        for i, (title, snippet, link) in enumerate(results, 1):  # 编号渲染每一条结果
            lines.append(f"{i}. {title}\n   {snippet}\n   {link}")
        return "\n".join(lines)  # 纯文本返回给模型

    return Tool(
        name="search_web",
        description=(
            # 提示词：搜索公开网络并返回最相关的结果（标题、摘要、URL）。
            # 当用户问及时事、事实、日程或任何你尚不知道的事情时使用——
            # 然后根据你找到的内容采取行动（例如创建日历事件）。
            "Search the public web and get back the top results (title, snippet, URL). "
            "Use when the user asks about current events, facts, schedules, or anything "
            "you don't already know — then act on what you find (e.g. create calendar events)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                # 提示词：搜索查询
                "query": {"type": "string", "description": "the search query"},
                # 提示词：返回多少条结果（默认 5）
                "max_results": {"type": "integer", "description": "how many results (default 5)"},
            },
            "required": ["query"],
        },
        fn=search_web,
    )
