"""追踪——每次运行一条追踪（LLM-Ops 框，第一步）。

同一批事件的两个输出：

1. JSONL，始终开启：每一轮对话都会把可读的行追加到
   .waku/traces/<date>.jsonl。追踪就是「按顺序发生了什么」——
   打开文件，读读你智能体的心思。零依赖。

2. OpenTelemetry spans，当设置了 OTEL_EXPORTER_OTLP_ENDPOINT 时：同一批事件
   变成任意 OTel 后端都能渲染的 span 树。用于本地 dashboard：

       pip install 'waku-agent[tracing]'
       phoenix serve                                # localhost:6006
       OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 python -m waku

   Langfuse 云端也说 OTel——把端点和认证头指到那里即可。
   下面的插桩代码既不知道也不在乎用的是哪一个。
"""

from __future__ import annotations

import json                                    # 序列化追踪记录为 JSON 行
from collections.abc import Iterator           # 迭代器类型注解
from contextlib import contextmanager          # turn() 的上下文管理器装饰器
from datetime import datetime, timezone        # 生成 UTC 时间戳
from pathlib import Path

from waku.config import Settings               # 配置对象（提供 home / otel 端点）


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")   # UTC，精确到毫秒


class TraceEncodingError(UnicodeError):
    """旧的追踪文件无法安全地以 UTF-8 读取或追加。"""

    def __init__(self, path: Path):
        self.path = path                       # 记下出问题的文件，供报错用
        super().__init__(
            f"Trace file is not valid UTF-8: {path}. It may have been written by an older "
            "Waku version using the Windows system encoding. Move it out of the traces directory "
            "and keep it as a backup, then restart Waku if it is today's trace. The file "
            "was not modified."
        )


def iter_trace_lines(path: Path) -> Iterator[str]:
    """逐条产出 UTF-8 追踪行，遇到旧文件时给出有用的报错。"""
    try:
        with path.open("r", encoding="utf-8") as trace:
            yield from trace                   # 逐行产出
    except UnicodeDecodeError as exc:
        raise TraceEncodingError(path) from exc   # 把解码错误转成更友好的异常


class Tracer:
    """兼作循环的观察者：在任意需要观察者的地方传入 `tracer.event`，
    每个循环步骤都会落进追踪。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = settings.home / "traces" / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        # 按日期命名：今天的追踪追加到今天的文件
        self._otel_tracer = self._init_otel(settings)   # 设置了 OTel 端点才初始化
        self._span_ctx = None                   # 当前根 span 的上下文（无 OTel 时为 None）
        self._trace_encoding_checked = False    # 只做一次编码校验

    def _init_otel(self, settings: Settings):
        if not settings.otel_endpoint:          # 未配置 OTLP 端点 → 纯 JSONL 模式
            return None
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider = TracerProvider(resource=Resource.create({"service.name": "waku-agent"}))
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_endpoint, insecure=True))
            )   # 批量导出到 OTLP 端点
            trace.set_tracer_provider(provider) # 设为全局 tracer provider
            self._otel_provider = provider      # 保存引用，便于每轮 force_flush
            return trace.get_tracer("waku")     # 拿到具名 tracer
        except ImportError:
            print("(tracing) OTEL endpoint set but opentelemetry not installed — "
                  "pip install 'waku-agent[tracing]'. JSONL tracing still on.")
            return None                         # 缺依赖时优雅降级为仅 JSONL

    def _write(self, record: dict) -> None:
        # 较早的 Windows 版本可能用 GBK 创建了这个每日文件。
        # 拒绝制造混合编码的 JSONL 文件：校验一次，解释如何保留旧文件，
        # 绝不猜测或改写用户数据。
        if not self._trace_encoding_checked:    # 每个进程只校验一次
            if self.path.exists():
                for _ in iter_trace_lines(self.path):   # 能完整读出 UTF-8 即通过
                    pass
            self._trace_encoding_checked = True
        record["ts"] = _now()                   # 盖上时间戳
        with self.path.open("a", encoding="utf-8") as f:   # 追加模式
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def _record_usage(self, event: dict) -> None:
        """把一次 LLM 调用的 token 用量追加到永久的台账（usage.jsonl）。
        与追踪（可为干净的演示而重置）不同，这是你实际花费的持续记录——
        绝不清除，在 dashboard 上按天汇总。token 是事实依据；美元成本由它们
        推导而来（价格可能变化），所以我们要存 token + 模型提供方/模型。"""
        usage = event.get("usage", {})          # 提取 token 用量
        record = {"ts": _now(), "provider": self.settings.provider,
                  "model": self.settings.model or "", "kind": "loop",
                  "in": usage.get("in", 0), "out": usage.get("out", 0)}
        with (self.settings.home / "usage.jsonl").open("a") as f:
            f.write(json.dumps(record) + "\n")  # 逐行追加

    # ---- 观察者：循环对每个 llm/tool/gate/... 事件都会调用它
    def event(self, kind: str, event: dict) -> None:
        if kind == "text":
            return  # 流式的 token 增量是给实时 UI 的，不写入追踪
        if kind == "llm":
            self._record_usage(event)           # 顺手记入永久台账
            # 盖个戳记录是哪个「大脑」回答的——在多模型世界里（对比评测、
            # 实时切换模型），一条没有模型信息的追踪只是半条追踪
            event = {"provider": self.settings.provider,
                     "model": self.settings.model or "", **event}
        self._write({"type": kind, **event})    # 写入 JSONL
        if self._otel_tracer and self._span_ctx is not None:   # OTel 已启用且在轮次内
            with self._otel_tracer.start_as_current_span(      # 开一个当前事件 span
                f"{kind}.{event.get('tool', event.get('decision', ''))}".rstrip("."),
                attributes={
                    "openinference.span.kind": {"llm": "LLM", "tool": "TOOL"}.get(kind, "CHAIN"),
                    **{f"waku.{k}": json.dumps(v, default=str) for k, v in event.items()},
                },   # 标注 span 类型 + 把事件字段序列化进属性
            ):
                pass   # 空体：span 的生命周期就是这一切

    # ---- 一次运行 = 一个根 span + turn_start/turn_end JSONL 标记
    @contextmanager
    def turn(self, user_message: str):
        self._write({"type": "turn_start", "user_message": user_message})   # 标记轮次开始
        if self._otel_tracer:
            with self._otel_tracer.start_as_current_span(
                "agent_run",
                attributes={"openinference.span.kind": "AGENT", "waku.user_message": user_message},
            ) as span:
                self._span_ctx = span           # 记住根 span，供事件挂靠
                try:
                    yield self                  # 让循环在根 span 内运行
                finally:
                    self._span_ctx = None       # 无论如何都清空，避免跨轮次串线
        else:
            yield self                          # 无 OTel 时纯 JSONL

    def end_turn(self, reply: str, iterations: int) -> None:
        self._write({"type": "turn_end", "reply": reply, "iterations": iterations})  # 标记轮次结束
        if getattr(self, "_otel_provider", None):
            # 每轮都冲刷一次：即使进程被杀死，追踪也应该保留下来
            self._otel_provider.force_flush(timeout_millis=2000)


def compose(*observers) -> callable:
    """把一个循环事件扇出给多个观察者（网关展示 + 追踪器）。"""
    active = [o for o in observers if o]        # 过滤掉 None / 假值观察者
    def fanout(kind: str, event: dict) -> None:
        for obs in active:                      # 逐个转发给每个观察者
            obs(kind, event)
    return fanout
