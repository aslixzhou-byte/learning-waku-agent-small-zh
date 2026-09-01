"""基于 Notion 数据库的情景记忆适配器。
设置环境变量：
    NOTION_TOKEN=<集成令牌>
    NOTION_EPISODES_DATABASE_ID=<数据库 id>
不做详细说明
"""

from __future__ import annotations  # 让类型注解（如 str | None）在旧版 Python 里也能用

import os  # 读取 NOTION_TOKEN / NOTION_EPISODES_DATABASE_ID 环境变量
import re  # 正则：从用户粘贴的 URL 中提取 32 位数据库 ID
from urllib.parse import urlparse  # 解析链接，判断它是裸 ID 还是完整 URL


def normalize_database_id(value: str) -> str:
    """接受一个 Notion 数据库 ID，或一个复制来的数据库 URL。
    Notion 把数据库对象 ID 放在 URL 路径中，把视图 ID 放在 ``v`` 查询参数中。
    把这段转换放在这里，让每个入口都能接受用户从 Notion 自然复制的链接。
    """
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        match = re.search(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])", parsed.path.lower())
        if not match:
            raise ValueError("Notion database link must include a database ID in its path")
        return match.group(0)  # 只返回裸的数据库 ID
    return value  # 本身就是裸 ID，原样返回


class NotionEpisodeStore:
    def __init__(self, token: str | None = None, database_id: str | None = None) -> None:
        from notion_client import Client
        self.token = token or os.environ.get("NOTION_TOKEN")
        raw_database_id = database_id or os.environ.get("NOTION_EPISODES_DATABASE_ID", "")
        self.database_id = normalize_database_id(raw_database_id) if raw_database_id else ""
        if not self.token:
            raise ValueError(
                "Notion token required: pass token= or set NOTION_TOKEN environment variable"
            )
        if not self.database_id:
            raise ValueError(
                "Notion database_id required: pass database_id= or set "
                "NOTION_EPISODES_DATABASE_ID environment variable"
            )
        self.client = Client(auth=self.token)  # 初始化 Notion API 客户端
        # notion-client >= 2.5（Notion API 2025-09-03）：行数据位于数据库的
        # data source 之下，所以先把 database_id 解析为 data_source_id。
        sources = self.client.databases.retrieve(database_id=self.database_id).get(
            "data_sources"
        ) or []
        if not sources:
            raise ValueError(
                f"Notion database {self.database_id} has no data sources to query"
            )
        self.data_source_id = sources[0]["id"]  # 取第一个 data source 的 id，后续读写都挂它

    def add(self, summary: str, happened_at: str) -> None:
        """在配置好的 Notion 数据库中创建一页新的 episode。"""
        self.client.pages.create(
            parent={"data_source_id": self.data_source_id},  # 新版 API 页面挂在 data source 下
            properties={
                "Name": {"title": [{"text": {"content": happened_at}}]},  # 标题列存时间戳
                "Summary": {"rich_text": [{"text": {"content": summary}}]},  # 富文本列存摘要
            },
        )

    def recent(self, top_k: int = 3) -> list[str]:
        """返回最新的 N 个 episode，按 happened_at 最新在前排序。"""
        pages = self._query_all()
        pages.sort(  # 按标题（即 ISO 时间戳字符串）降序排，最新在前
            key=lambda p: self._extract_title(p.get("properties", {}).get("Name", {})),
            reverse=True,
        )
        return [self._format(p) for p in pages[:top_k]]  # 只取前 N 个并格式化成纯文本

    def search(self, query: str, top_k: int = 3) -> list[str]:
        """对 episode 摘要做关键词搜索；查询为空时回退到 recent。"""
        keywords = re.findall(r"[a-zA-Z0-9]{2,}", query.lower())  # 分词：至少 2 个字母数字的 token
        if not keywords:
            return self.recent(top_k)  # 空查询没有可搜的词，直接回退到最近事件

        # 去重并保持原有顺序。
        keywords = list(dict.fromkeys(keywords))  # dict 去重但保留首次出现顺序
        matches = []
        for page in self._query_all():  # 逐页过滤（个人数据量小，不值得建索引）
            summary_lower = self._extract_rich_text(
                page.get("properties", {}).get("Summary", {})
            ).lower()
            if any(  # 摘要里命中任一关键词即算匹配；\b 保证整词匹配，re.escape 转义特殊字符
                re.search(rf"\b{re.escape(keyword)}\b", summary_lower)
                for keyword in keywords
            ):
                matches.append(page)

        matches.sort(  # 命中的结果同样按时间降序
            key=lambda p: self._extract_title(p.get("properties", {}).get("Name", {})),
            reverse=True,
        )
        return [self._format(p) for p in matches[:top_k]]  # 取前 N 条

    def list(self, limit: int = 200) -> list[dict]:
        """以 dict 列表返回所有 episode，按 happened_at 降序排列。"""
        pages = self._query_all()
        pages.sort(
            key=lambda p: self._extract_title(p.get("properties", {}).get("Name", {})),
            reverse=True,
        )
        return [  # 转成与 SQLite 版一致的 dict 形状，供 dashboard 无差别渲染
            {
                "id": page.get("id", ""),
                "happened_at": self._extract_title(
                    page.get("properties", {}).get("Name", {})
                ),
                "summary": self._extract_rich_text(
                    page.get("properties", {}).get("Summary", {})
                ),
                "created_at": page.get("created_time", ""),
            }
            for page in pages[:limit]
        ]

    def delete(self, episode_id: int | str) -> bool:
        """归档指定 episode id 对应的 Notion 页面。"""
        self.client.pages.update(page_id=str(episode_id), archived=True)  # Notion 只能归档，不能真正删除
        return True

    def _query_all(self) -> list[dict]:
        """按分页机制获取数据库中的所有页面。"""
        results: list[dict] = []
        response = self.client.data_sources.query(data_source_id=self.data_source_id)  # 取第一页
        results.extend(response.get("results", []))
        while response.get("has_more"):  # 只要还有下一页就继续翻
            response = self.client.data_sources.query(
                data_source_id=self.data_source_id,
                start_cursor=response.get("next_cursor"),  # 用上一页返回的光标接着取
            )
            results.extend(response.get("results", []))
        return results

    def _format(self, page: dict) -> str:
        props = page.get("properties", {})
        happened_at = self._extract_title(props.get("Name", {}))  # 时间戳取自标题列
        summary = self._extract_rich_text(props.get("Summary", {}))  # 摘要取自富文本列
        return f"({happened_at}) {summary}"  # 拼成 "(时间) 摘要" 的纯文本行，供注入提示词

    @staticmethod
    def _extract_title(prop: dict) -> str:
        # Notion 标题是富文本片段列表，逐个取出 content 拼成一个字符串
        return "".join(part.get("text", {}).get("content", "") for part in prop.get("title", []))

    @staticmethod
    def _extract_rich_text(prop: dict) -> str:
        # 富文本同样是片段列表，缺字段时用空串兜底
        return "".join(
            part.get("text", {}).get("content", "") for part in prop.get("rich_text", [])
        )
