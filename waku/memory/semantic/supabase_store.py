"""语义记忆，向量版 Supabase pgvector 升级路径。

不做详细说明
"""

from __future__ import annotations

import os
import uuid

from waku.config import Settings


class SupabaseFactStore:
    def __init__(self, settings: Settings):
        import openai
        from supabase import create_client

        self.supabase = create_client(
            os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"]
        )
        self.openai = openai.OpenAI()
        self.embed_model = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
        self.top_k = settings.retrieval_top_k

    def _embed(self, text: str) -> list[float]:
        # input 必须传列表，结果里取第 0 个向量的 embedding（1536 维浮点列表）
        return self.openai.embeddings.create(model=self.embed_model, input=[text]).data[0].embedding

    def add(self, subject: str, content: str, source: str = "user") -> None:
        # launch-rag 的列映射：source=subject，text=事实本体
        self.supabase.table("rag_chunks").upsert(  # upsert：同 chunk_id 存在就覆盖，保证幂等
            {
                "chunk_id": f"fact-{uuid.uuid4().hex[:12]}",  # 随机前缀 + 12 位十六进制，几乎不会撞
                "source": subject.lower().strip(),  # 主题归一化后放进 source 列
                "text": content,
                "embedding": self._embed(f"{subject}: {content}"),  # 主题+正文一起编码，检索命中更准
            },
            on_conflict="chunk_id",
        ).execute()

    def search(self, query: str, top_k: int = 4) -> list[str]:
        result = self.supabase.rpc(  # 调用 Postgres 侧 match_chunks 函数做余弦相似度排序
            "match_chunks",
            {"query_embedding": self._embed(query), "match_count": top_k},
        ).execute()
        return [f"[{row['source']}] {row['text']}" for row in (result.data or [])]
        # 返回格式与 SqliteFactStore.search 完全一致，上层无需区分用的是哪种存储
