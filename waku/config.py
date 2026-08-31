"""配置 —— 每个开关都是一个环境变量，已在 .env.example 中说明。

没有配置框架：一个 dataclass，在启动时读取一次。能读懂这个文件，你就知道 Waku 可配置的全部能力。
"""

from __future__ import annotations  # 让类型注解（如 Path | None）在旧版 Python 里也能用

import os                             # 读环境变量（所有配置项的来源）
from dataclasses import dataclass, field  # dataclass：自动生成 __init__；field：给默认值配工厂
from pathlib import Path               # 跨平台路径对象（home 目录等）

from dotenv import load_dotenv  # 从 .env 文件加载环境变量（可选依赖）

load_dotenv()  # 读取当前目录下的 .env（若存在）


@dataclass
class Settings:
    # --- LLM：选一个 provider，设置它的 key。参见 waku/loop/models.py 的 PROVIDERS。
    provider: str = field(default_factory=lambda: os.getenv("WAKU_PROVIDER", "anthropic"))  # 默认 Anthropic
    # 显式覆盖（可选）：key、endpoint、模型 id。留空则使用 provider 自身的 key 环境变量和默认模型。
    api_key: str = field(default_factory=lambda: os.getenv("WAKU_API_KEY", ""))  # 显式 key；留空则读 provider 的
    base_url: str | None = field(default_factory=lambda: os.getenv("WAKU_BASE_URL") or None)  # 自定义端点（代理/网关）
    model: str = field(default_factory=lambda: os.getenv("WAKU_MODEL", ""))  # 主模型；留空则用 provider 默认
    # 检索门禁和记忆整合摘要器使用的廉价模型。
    small_model: str = field(default_factory=lambda: os.getenv("WAKU_SMALL_MODEL", ""))  # 门禁/整合用廉价模型

    # --- Home：Waku 存放状态的位置（记忆数据库、日历、发件箱、追踪）。
    # 默认是运行目录旁的 ./.waku，这样你能打开它写下的每个文件。
    # 本地优先意味着你随时可以查看。
    home: Path = field(default_factory=lambda: Path(os.getenv("WAKU_HOME", ".waku")))  # 默认 ./waku

    # --- 循环护栏
    max_iterations: int = field(default_factory=lambda: int(os.getenv("WAKU_MAX_ITERATIONS", "10")))  # 推理-行动循环上限
    # 余量对 REASONING 模型（kimi-k3、gpt-5.x、gemini-*-pro）很重要：
    # 它们会在回答前消耗输出 token 来思考，上限太低会让它们在思考中途就 hit
    # stop_reason=max_tokens，返回一个空回复（曾亲眼看到 kimi-k3 在 2048 时就这样）。
    # 8192 留出了「思考 + 回答」的空间；它是上限而非目标，高效的模型开销不变。
    max_tokens: int = field(default_factory=lambda: int(os.getenv("WAKU_MAX_TOKENS", "8192")))  # 单次输出上限
    # 工作记忆是一个滑动窗口（就像上下文 RAM）：只有最近 N 轮进入提示词。
    # 更早的轮次不会丢失 —— 它们在 state.db 里，被整合提炼成事实，
    # 在相关时由检索门禁拉回。没有这个上限，长对话（尤其是常驻的 Telegram
    # 会话）每轮都会重发全部历史，直到爆掉。
    history_turns: int = field(default_factory=lambda: int(os.getenv("WAKU_HISTORY_TURNS", "12")))  # 工作记忆窗口轮数

    # --- 记忆
    # 每累积 N 次新交流后执行一次整合（把聊天提炼成持久事实）。
    consolidate_every: int = field(default_factory=lambda: int(os.getenv("WAKU_CONSOLIDATE_EVERY", "6")))  # 整合触发间隔
    retrieval_top_k: int = field(default_factory=lambda: int(os.getenv("WAKU_RETRIEVAL_TOP_K", "4")))  # 检索返回条数上限
    # 'sqlite'（默认，零配置）或 'supabase'（pgvector 升级路径 —— 参见 launch-rag）。
    semantic_store: str = field(default_factory=lambda: os.getenv("WAKU_SEMANTIC_STORE", "sqlite"))  # 语义记忆后端
    # 'sqlite'（默认，零配置）或 'notion'（情景记忆存到 Notion 数据库）。
    episodic_store: str = field(default_factory=lambda: os.getenv("WAKU_EPISODIC_STORE", "sqlite"))  # 情景记忆后端

    # --- 工具
    # 通过 AppleScript 把创建的事件同步到 Apple Calendar（一个专用的 "Waku" 日历）。
    # 需要显式开启，因为它会写入你真实的日历应用。
    apple_calendar: bool = field(
        default_factory=lambda: os.getenv("WAKU_APPLE_CALENDAR", "") in ("1", "true", "yes")
    )  # 显式开启才同步 Apple Calendar（默认关）
    # 赋予 agent 对 Apple Calendar、Mail、Reminders、Notes 的读写权限
    # （macOS；首次使用会触发系统的「自动化」权限弹窗）。
    apple_tools: bool = field(
        default_factory=lambda: os.getenv("WAKU_APPLE_TOOLS", "") in ("1", "true", "yes")
    )  # 显式开启才暴露苹果套件工具（默认关）
    # 注册实验性工具（delegate_task -> pi 子 agent 等）。环境变量是全局开关；
    # 竞技场按单场比赛设置它，让编码赛能把活交给 pi 而无需为整个进程打开它。
    experimental: bool = field(
        default_factory=lambda: os.getenv("WAKU_EXPERIMENTAL", "") in ("1", "true", "yes")
    )  # 实验性工具开关（默认关）

    # --- 可选网关
    telegram_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))  # 为空则不启用 Telegram

    # --- 追踪（JSONL 始终开启；设置了 endpoint 才导出 OTel）
    otel_endpoint: str = field(
        default_factory=lambda: os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    )  # OpenTelemetry 导出端点；留空则只写 JSONL

    def ensure_home(self) -> Path:
        """建出状态目录树（home/traces/outbox），缺哪个建哪个，幂等。"""
        self.home.mkdir(parents=True, exist_ok=True)  # 建 home 本身（.waku/），可多级
        (self.home / "traces").mkdir(exist_ok=True)   # 建追踪目录（JSONL 输出落这里）
        (self.home / "outbox").mkdir(exist_ok=True)   # 建发件箱（send_message 草稿落这里）
        return self.home


def load_settings() -> Settings:
    return Settings()  # 启动时读一次环境变量，拼出全部配置；没有额外逻辑
