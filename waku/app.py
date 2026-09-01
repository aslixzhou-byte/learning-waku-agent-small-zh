"""装配 把各个部件组装成一个 Waku。
网关调用 respond()
config → db → tools → memory → session → loop。
"""

from __future__ import annotations

from waku.config import Settings, load_settings
from waku.db import connect
from waku.loop.agent import LoopResult, Observer, run_loop
from waku.loop.models import get_client
from waku.ops.tracing import Tracer, compose
from waku.runtime.session import Session
from waku.tools import build_registry


class Waku:
    def __init__(self, settings: Settings | None = None, client=None, conn=None):
        # `client` 和 `conn` 都是可注入的：评估会换入一个脚本化的模型，
        # dashboard 会注入一个跨线程的连接。无论哪种都是同一个接缝。
        self.settings = settings or load_settings()  # 没注入配置就现场读 .env 建一个
        self.settings.ensure_home()                  # 建出 .waku/ 及 traces、outbox 子目录
        self.conn = conn or connect(self.settings.home)  # 没注入连接就打开/创建 state.db
        self.client = client or get_client(self.settings)  # 没注入客户端就按 provider 建 LLM 客户端

        # 先建记忆：记忆管理工具需要它。
        from waku.memory import Memory  # 局部导入避免循环依赖

        self.memory = Memory(self.conn, self.settings, self.client)  # 记忆（语义/情景/程序性）最先接好
        self.tools = build_registry(self.conn, self.settings, self.memory)  # 工具依赖记忆，所以后建
        self.mcp_bridge = getattr(self.tools, "mcp_bridge", None)  # MCP 桥（可选能力；没有就为 None）
        self.session = Session(self.settings, memory=self.memory)  # 会话层挂在记忆之上
        self.tracer = Tracer(self.settings)  # 追踪器：记录每轮 LLM 事件
        print("WaKu初始化 --- HelloWorld")

    def close(self) -> None:
        """释放外部资源（MCP 子进程）。在 dashboard 因设置变更而重建 agent 时调用。"""
        if self.mcp_bridge is not None:  # 只关真实存在的桥，避免对 None 解引用
            self.mcp_bridge.close()

    def respond(self, user_message: str, observer: Observer | None = None,
                source: str = "cli", stream: bool = False) -> LoopResult:
        """一整轮：组装工作记忆 → 跑循环 → 持久化。
        `source` 标记消息是从哪个网关进来的（cli / voice / telegram / dashboard），
        这样统一聊天界面能显示它的来源。
        `stream=True` 会把回复文本逐 token 流式发给观察者。
        发生的一切既被展示（observer）也被记录（tracer）。"""
        # 在门禁决策流过时捕获它，好把它和这一轮一起持久化
        # （dashboard 实时展示的「重新打开线程」遥测）。
        import time  # perf_counter 计时本轮延迟

        captured: dict = {}  # 存本轮被捕获的门禁决策（决策 + 原因）

        def _capture(kind, ev):  # 观察者链的第三段：把 gate 事件捞进 captured
            if kind == "gate":
                captured["gate"] = {"decision": ev.get("decision"), "reason": ev.get("reason")}
        notify = compose(observer, self.tracer.event, _capture)  # 把网关观察者、追踪、捕获串成一条链
        t0 = time.perf_counter()  # 起表，用于统计本轮延迟

        print("user input: {}".format(user_message))

        with self.tracer.turn(user_message):  # 进入本轮追踪上下文（结束时记一个 turn 事件）
            system = self.session.build_system(user_message, notify=notify)  # 组装系统提示词（人设+记忆+时间）
            # 工作记忆是一个有界窗口：只有最近 N 轮（每轮 2 行）进入提示词，
            # 这样无论对话持续多久，上下文/成本/延迟都保持平稳。更早的轮次
            # 留在 state.db，在相关时经检索门禁 + 情景记忆回来。
            window = self.settings.history_turns * 2  # 每轮占 user+assistant 两条消息
            messages = self.session.history[-window:] + [{"role": "user", "content": user_message}]  # 最近窗口 + 本条新消息

            print("本轮 message: ")
            for msg in messages:
                print(f"Role: {msg['role']}, Content: {msg['content']}")

            print("即将进入 run_loop...")

            result = run_loop(  # 跑核心循环：观察 → 推理 → 行动 → 重复
                client=self.client,
                model=self.settings.model,
                system=system,
                messages=messages,
                tools=self.tools,
                max_iterations=self.settings.max_iterations,
                max_tokens=self.settings.max_tokens,
                observer=notify,
                stream=stream,
            )

            def _status(out: str) -> str:  # 粗判工具结果成败：含 failed/timed out/以 error 开头都算 error
                low = (out or "").lower()
                return "error" if ("failed" in low or "timed out" in low
                                   or low.startswith("error")) else "ok"
            meta = {  # 本轮遥测，随 assistant 行一起存进 chat_log
                "gate": captured.get("gate"),  # 门禁决策（可能为 None）
                "iterations": result.iterations,  # 实际轮数
                "latency_ms": int((time.perf_counter() - t0) * 1000),  # 本轮总耗时（毫秒）
                "tools": [{"tool": c["tool"], "status": _status(c["output"])}  # 每个工具的成败状态
                          for c in result.tool_calls],
                # 这一轮是哪个大脑回答的 —— 这样重新打开的线程（或中途切换过
                # 模型的线程）能在每张卡片上显示出来。
                "model": self.settings.model,
                "provider": self.settings.provider,
            }

            print("本轮 Run loop 结束")
            from pprint import pprint
            pprint(meta)

            print("session.add_exchange 记录工作记忆")

            self.session.add_exchange(user_message, result.reply, tool_calls=result.tool_calls,  # 记入工作记忆 + 聊天日志
                                      source=source, meta=meta)

            if self.memory is not None:
                self.memory.maybe_consolidate(notify=notify)  # 满 N 轮就做一次记忆整合
                self.memory.export_markdown()   # 保持 MEMORY.md 同步

        self.tracer.end_turn(result.reply, result.iterations)  # 结束本轮追踪：记最终回复与轮数
        return result
