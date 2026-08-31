"""语义记忆，向量版 —— Supabase pgvector 升级路径。

接口与 SqliteFactStore 相同，不同的是检索方式：用真正的 embedding 和余弦相似度
替代关键词 BM25。使用与 launch-rag / launch-agentic-rag
(github.com/ShenSeanChen/launch-agentic-rag) 完全相同的 schema 和 `match_chunks`
RPC —— 如果你看过那些视频，这就是同一张表。先在全新项目上运行
sql/init_supabase.sql，然后：

    pip install 'waku-agent[supabase]'
    WAKU_SEMANTIC_STORE=supabase  SUPABASE_URL=...  SUPABASE_SERVICE_KEY=...
    OPENAI_API_KEY=...   # 仅用于 embedding（text-embedding-3-small，1536d）

什么时候值得用它而不是 FTS5？当措辞与字面表达不一致时：
"my business partner" 应该能找到 "Alex is my cofounder"。关键词做不到；
向量可以。对几百条个人事实来说，两者都是瞬间完成。
"""

from __future__ import annotations  # 让类型注解（如 Settings）在旧版 Python 里也能用

import os  # 读取 SUPABASE_URL / SERVICE_KEY / OPENAI_API_KEY 等环境变量
import uuid  # 生成唯一的 chunk_id（幂等 upsert 的键）

from waku.config import Settings  # 配置：读取检索 top_k


class SupabaseFactStore:
    def __init__(self, settings: Settings):
        import openai  # 延迟导入：openai 与 supabase 都放在 [supabase] extra 里，默认不装
        from supabase import create_client

        self.supabase = create_client(  # 用服务端密钥连接（可读可写，绕过 RLS）
            os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"]
        )
        self.openai = openai.OpenAI()  # 构造客户端，自动读取 OPENAI_API_KEY
        self.embed_model = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")  # 默认官方 1536 维 embedding 模型
        self.top_k = settings.retrieval_top_k  # 检索条数跟随全局配置

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
