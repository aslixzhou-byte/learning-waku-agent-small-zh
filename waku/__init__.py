"""waku-agent
四大支柱，各对应一个模块：
  harness  → waku/runtime + waku/gateway  （围绕裸 LLM 的脚手架）
  loop     → waku/loop                      （观察 → 推理 → 行动 → 重复）
  memory   → waku/memory                    （程序性 / 语义 / 情景）
  ops      → waku/ops + evals/              （追踪 → 评估 → 门禁 → 发布）
"""

__version__ = "0.1.0"  # 包版本号（发布/追溯用；与 .env 无关）
