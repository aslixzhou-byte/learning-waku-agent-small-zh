"""Dashboard——把每一根支柱都放到同一个本地页面上。零新增依赖。

    make dashboard        # → http://localhost:7777

一个仅用标准库的 HTTP 服务器，读取 Waku 已经在写的那些文件：
  循环 + 框架/运行时   traces/*.jsonl   （对话轮次、门禁决策、工具调用、token）
  记忆               state.db         （事实、情景、聊天记录、记忆整合）
  工具               state.db + calendar.ics + outbox/
  评测               eval_report.json （由 `make gate` 写入）

概览页镜像了架构图——每个框都可点击，点击后打开对应模块的实时数据。
聊天停靠区是一个真正的网关：输入（或说出）一条消息，就能看到 CLI / voice /
telegram 这些网关驱动的那同一个框架/运行时（门禁、循环、工具、记忆）
在浏览器里运行并点亮。

前端就是普通的静态文件（static/index.html + style.css + app.js），
原样提供——没有构建步骤，没有框架。这个文件只是服务器 + API。
只绑定 127.0.0.1。想看深的追踪瀑布请用 Phoenix（`make trace`）。
"""

from __future__ import annotations

import json        # 序列化 API 响应、解析前端 JSON 载荷
import os          # 读取环境变量（端口、会话空闲、API 密钥等）
import threading   # 锁：串行化共享智能体与 SSE 事件
import time        # 缓存 TTL 判断
from datetime import datetime, timezone   # UTC 时间戳与空闲判定
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer   # 标准库 HTTP 服务器
from pathlib import Path                  # 文件系统路径

from waku.config import load_settings     # 读取 / 构建配置
from waku.db import connect               # 打开 state.db
from waku.ops import compare_history, judge as judge_mod, scoring   # 竞技场历史 / 裁判 / 完成度
from waku.ops.tracing import TraceEncodingError, iter_trace_lines   # 安全读取追踪文件

PORT = 7777        # 默认端口；被占用时向上顺延
# 前端住在它自己的文件里（static/index.html + style.css + app.js），
# 由这个标准库服务器原样提供——没有构建步骤，没有框架。改那些文件来
# 调整 UI；改这个文件来调整服务器/API。
STATIC = Path(__file__).resolve().parent / "static"

# 浏览器网关共用的一个智能体。懒加载（第一次聊天时构建），通过一个
# 跨线程连接 + 一把锁在带线程的服务器工作线程间复用，所以聊天是
# 一次只跑一个——对单用户本地工具来说是正确的。
_agent = None
_agent_lock = threading.Lock()
_dashboard_session = None  # 本次 dashboard 运行的聊天线程（带日期；刷新期间保持稳定）


def _dash_session() -> str:
    """新的 dashboard 聊天所属的线程。每个进程只解析一次：
    RESUME（恢复）最近的 dashboard 线程（这样重启后聊天仍留在屏幕上），
    否则新开一个带日期的。绝不用那个永恒的 'default'。"""
    global _dashboard_session
    if _dashboard_session is None:              # 每个进程只解析一次
        try:
            conn = connect(load_settings().home)  # 打开数据库来查最近线程
            _dashboard_session = _resume_or_new_session(conn)  # 恢复或新建
            conn.close()                        # 用完即关
        except Exception:                       # 数据库打不开时退化为新的带日期会话
            _dashboard_session = datetime.now().strftime("dashboard-%Y%m%d-%H%M%S")
    return _dashboard_session


def _resume_or_new_session(conn) -> str:
    """选定本次运行的线程：如果最近的 dashboard 线程的最后一条消息仍然
    新鲜（在空闲窗口内），就 RESUME（恢复）它，否则新开一个带日期的。
    没有这个逻辑，每次服务器重启都会铸造一个全新的空线程，可见的聊天
    就「消失」了（其实只是停靠在旧的 id 下面）。空闲时间过长仍然会轮换——
    那是在运行起来之后 _maybe_rotate_session 的职责。"""
    idle_min = int(os.getenv("WAKU_SESSION_IDLE_MINUTES", "60"))
    # 按来源而非 id 前缀匹配：「+ New chat」会生成 's-...' 的 id，所以
    # 用 'dashboard-%' 过滤会在重启时把这些线程孤立掉。每条 dashboard
    # 消息都带有 source='dashboard' 标记——这才是可靠的信号。
    row = conn.execute(
        "SELECT session_id, MAX(created_at) AS last_at FROM chat_log "
        "WHERE source='dashboard' GROUP BY session_id "
        "ORDER BY last_at DESC LIMIT 1"
    ).fetchone()
    if row and row["last_at"]:
        try:
            last = datetime.strptime(row["last_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            if idle_min <= 0 or (datetime.now(timezone.utc) - last).total_seconds() <= idle_min * 60:
                return row["session_id"]
        except ValueError:
            pass
    return datetime.now().strftime("dashboard-%Y%m%d-%H%M%S")


def _get_agent():
    global _agent, _dashboard_session
    if _agent is None:
        from waku.app import Waku

        settings = load_settings()
        settings.ensure_home()
        conn = connect(settings.home, check_same_thread=False)
        _agent = Waku(settings=settings, conn=conn)
        # 一次 dashboard 运行会恢复其最近的线程（这样重启/刷新后聊天仍
        # 留在屏幕上），如果那个线程已空闲则重新开始。collect() 上报的
        # 是同一个 id，所以停靠区能恢复正确的对话。
        _dashboard_session = _resume_or_new_session(conn)
        _agent.session.session_id = _dashboard_session
    return _agent


def _maybe_rotate_session(agent) -> None:
    """回归的用户应该拿到一条「新」线程，而不是上周的。如果当前会话的
    最新一条消息比 WAKU_SESSION_IDLE_MINUTES（默认 60 分钟）更旧，
    就轮换到一个新的带日期的会话 id——旧线程在 History 里仍然一按即达。
    真实出现的 bug：一位测试者几天后回来，他的新聊天落进了一条
    一周前、有 32 条消息的旧线程。"""
    idle_min = int(os.getenv("WAKU_SESSION_IDLE_MINUTES", "60"))
    if idle_min <= 0:
        return
    row = agent.conn.execute("SELECT MAX(created_at) FROM chat_log WHERE session_id=?",
                             (agent.session.session_id,)).fetchone()
    if not row or not row[0]:
        return
    try:  # sqlite 的 datetime('now') 是 UTC 格式 "YYYY-MM-DD HH:MM:SS"
        last = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return
    if (datetime.now(timezone.utc) - last).total_seconds() > idle_min * 60:
        agent.session.start_new(datetime.now().strftime("dashboard-%Y%m%d-%H%M%S"))


def chat(message: str) -> dict:
    """通过框架/运行时跑一次真实的对话轮次，并返回结构化结果——门禁决策、
    工具调用、回复、延迟——这样浏览器能按实际发生的样子渲染流水线。
    和其它网关一样写入追踪 + 记忆。"""
    events: list[dict] = []                     # 收集本轮观察到的框架/运行时事件
    with _agent_lock:                           # 一次只跑一个对话轮次
        agent = _get_agent()                    # 取共享智能体（懒加载）
        _maybe_rotate_session(agent)            # 会话太久没活动就轮换
        start = datetime.now(timezone.utc)
        result = agent.respond(message, observer=lambda kind, ev: events.append({"kind": kind, **ev}),
                               source="dashboard")   # 观察者把每个事件记下来
        latency_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)  # 测总延迟

    gate = next((e for e in events if e["kind"] == "gate"), None)   # 取出门禁决策事件
    cons = next((e for e in events if e["kind"] == "consolidation"), None)   # 取记忆整合事件
    return {
        "reply": result.reply,
        "gate": {"decision": gate["decision"], "reason": gate.get("reason")} if gate else None,
        "tools": [
            {"tool": c["tool"], "args": c["args"], "output": c["output"],
             "status": _tool_status(c["output"]), "summary": (c["output"] or "").split(". ")[0][:120]}
            for c in result.tool_calls
        ],
        "consolidation": {"new_facts": cons["new_facts"]} if cons else None,
        "iterations": result.iterations,
        "latency_ms": latency_ms,
    }


def chat_stream(message: str, emit) -> None:
    """跑一个对话轮次，每发生一个框架/运行时事件就调用一次 emit(kind, event)——
    门禁决策、工具调用，以及逐 token 的回复文本——这样浏览器能像 CLI/voice
    那样实时流出「思考过程」。以携带最终结构化结果的 'done' 事件收尾。"""
    events: list[dict] = []                     # 只缓存 gate/consolidation 用于收尾

    def observer(kind, ev):
        if kind in ("gate", "consolidation"):   # 这两个事件要留到结束时组装
            events.append({"kind": kind, **ev})
        emit(kind, ev)                          # 其余事件立即转发给 SSE

    with _agent_lock:                           # 一次只跑一个对话轮次
        agent = _get_agent()
        _maybe_rotate_session(agent)
        start = datetime.now(timezone.utc)
        result = agent.respond(message, observer=observer, source="dashboard", stream=True)
        latency_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)  # 测总延迟

    gate = next((e for e in events if e["kind"] == "gate"), None)   # 门禁决策
    cons = next((e for e in events if e["kind"] == "consolidation"), None)   # 记忆整合
    emit("done", {
        "reply": result.reply,
        "gate": {"decision": gate["decision"], "reason": gate.get("reason")} if gate else None,
        "tools": [{"tool": c["tool"], "args": c["args"], "output": c["output"],
                   "status": _tool_status(c["output"]),
                   "summary": (c["output"] or "").split(". ")[0][:120]} for c in result.tool_calls],
        "consolidation": {"new_facts": cons["new_facts"]} if cons else None,
        "iterations": result.iterations,
        "latency_ms": latency_ms,
        "model": agent.settings.model,   # 是哪个「大脑」回答的——每张卡片都会显示
    })


def _compare_one(message: str, spec: str) -> dict:
    """让一条消息在一次性临时 home 里跑过一个模型（与 `make shootout`
    相同的隔离，所以绝不会碰到你的真实记忆/日历），并返回它的「回执」——
    回复、门禁、工具、延迟、token、成本。坏掉的参赛者返回 {error} dict；
    它绝不会抛出异常。"""
    import tempfile
    import time

    from waku.app import Waku
    from waku.config import Settings

    provider, _, model = spec.partition(":")    # 把 "provider:model" 拆成两半
    home = Path(tempfile.mkdtemp(prefix=f"compare-{provider}-"))   # 一次性隔离 home
    gate: dict = {}                             # 收集门禁决策
    try:
        settings = Settings(provider=provider, model=model, small_model="",
                            home=home, apple_calendar=False)
        app = Waku(settings=settings)
        t0 = time.perf_counter()
        result = app.respond(message, source="compare",
                             observer=lambda k, ev: gate.update(
                                 decision=ev.get("decision"), reason=ev.get("reason"))
                             if k == "gate" else None)
        ms = int((time.perf_counter() - t0) * 1000)
        tin = tout = 0
        ledger = home / "usage.jsonl"
        if ledger.exists():
            for line in ledger.read_text().splitlines():
                try:
                    r = json.loads(line)
                    tin, tout = tin + r.get("in", 0), tout + r.get("out", 0)
                except json.JSONDecodeError:
                    pass
        pin, pout = price_for(provider, settings.model)
        return {"spec": spec, "provider": provider, "model": settings.model, "reply": result.reply,
                "gate": (gate or None), "iterations": result.iterations, "latency_ms": ms,
                "tools": [{"tool": c["tool"]} for c in result.tool_calls],
                "tokens_in": tin, "tokens_out": tout,
                "cost_usd": round(tin / 1e6 * pin + tout / 1e6 * pout, 4)}
    except (Exception, SystemExit) as exc:   # 坏掉的参赛者（包括缺失密钥——
        # get_client 会为它抛 SystemExit）只让自己失败，不影响整场对比
        return {"spec": spec, "provider": provider, "model": model, "error": str(exc)[:200]}


def compare_models(payload: dict) -> dict:
    """让一条消息「同时」（并行线程）跑过多个模型，并一起返回所有结果。
    非流式；dashboard 用 SSE 版本，这样每一列完成时逐个填充。"""
    from concurrent.futures import ThreadPoolExecutor

    message = (payload.get("message") or "").strip()   # 去除首尾空白
    specs = payload.get("models") or []                # 参赛者 spec 列表
    if not message or not specs:                       # 缺任何一方都无法开跑
        return {"error": "message and models required"}
    with ThreadPoolExecutor(max_workers=min(len(specs), 6)) as ex:   # 并行（上限 6）
        results = list(ex.map(lambda s: _compare_one(message, s), specs))   # 每个参赛者各跑一次
    return {"ok": True, "message": message, "results": results}


def compare_stream(message: str, specs: list, emit, judge: bool = False,
                   coding: bool = False, judge_spec: str = "", apple: bool = False) -> None:
    """让模型们开跑，并实时流式输出每一个的框架/运行时——每个模型的门禁决策
    和工具调用——这样每一列都像聊天停靠区那样现场展开，而不是静态的
    'racing…'。每个参赛者都在自己隔离的临时 home 里运行真正的循环
    （含工具），所以它能创建事件 / 保存笔记 / 搜索，而不碰你的真实数据。
    并行线程共享一个 SSE 套接字，所以 emit() 在一把锁后面串行化；
    每个事件都带有自己的 `spec` 标记，供浏览器路由到正确的列。"""
    import tempfile
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    from waku.app import Waku
    from waku.config import Settings

    if not message or not specs:
        emit("done", {"error": "message and models required"})
        return

    lock = threading.Lock()
    collected: list = []   # 每个模型的结果，最后保存进对比历史
    # 如果这条提示词是已知的电池用例，每一列都会得到一个确定性的
    # 完成度分数（正确的工具有没有触发、参数对不对、次数够不够）。
    # 自由形式的提示词仍会开跑——只是没有分数。
    case = scoring.case_for_message(message)

    def send(kind, ev):
        with lock:                          # SSE 事件在锁后串行化，避免并发写坏套接字
            emit(kind, ev)
            if kind == "result":            # 把最终结果并入待保存的列表
                collected.append(ev)

    def run(spec):
        provider, _, model = spec.partition(":")   # 拆开 "provider:model"
        send("start", {"spec": spec, "provider": provider, "model": model})   # 先通知该列开跑
        home = Path(tempfile.mkdtemp(prefix=f"compare-{provider}-"))   # 每列独立临时 home
        gate: dict = {}                     # 收集本列的门禁决策

        # 实时流出「结构性」框架/运行时（门禁决策、工具调用）——这些事件来自
        # 观察者，不需要 stream=True。我们故意不对回复做逐 token 流式：
        # stream=True 会让某些推理模型（带工具的 gemini）要求
        # thought_signature 并返回 400，而普通路径不会。所以框架/运行时
        # 现场展开，回复在结束时落地。
        def obs(kind, ev):
            if kind == "gate":
                gate.update(decision=ev.get("decision"), reason=ev.get("reason"))
                send("gate", {"spec": spec, "decision": ev.get("decision"), "reason": ev.get("reason")})
            elif kind == "tool":
                send("tool", {"spec": spec, "tool": ev.get("tool")})

        try:
            # 编码模式注册了 delegate_task（pi 子智能体），这样循环就能把
            # 真实的编程任务交给 pi——运行的是完整的框架/运行时（门禁、记忆、
            # 工具），不是绕过。pi 跑在这张卡对应的模型上。
            # apple_calendar 默认关闭（为了隔离），按场次选择加入——开启时，
            # 每个模型都会向真实的「Waku」日历写入自己的事件。
            settings = Settings(provider=provider, model=model, small_model="",
                                home=home, apple_calendar=apple, experimental=coding)
            app = Waku(settings=settings)
            # 一个要评分的用例可能预加载一条事实（例如「applies memory」），
            # 这样每个模型都从清单所假定的同一状态出发。
            if case and case.get("setup_fact"):
                app.memory.facts.add(case["setup_fact"]["subject"], case["setup_fact"]["content"])
            t0 = time.perf_counter()
            result = app.respond(message, source="compare", observer=obs)
            ms = int((time.perf_counter() - t0) * 1000)
            tin = tout = 0
            ledger = home / "usage.jsonl"
            if ledger.exists():
                for line in ledger.read_text().splitlines():
                    try:
                        r = json.loads(line)
                        tin, tout = tin + r.get("in", 0), tout + r.get("out", 0)
                    except json.JSONDecodeError:
                        pass
            pin, pout = price_for(provider, settings.model)   # 每 M token 单价
            cost = round(tin / 1e6 * pin + tout / 1e6 * pout, 4)   # 成本 = token × 单价
            completion = None
            if case:
                passed, why = scoring.check_case(case, result.tool_calls)
                completion = {"passed": passed, "why": why, "case": case["id"]}
            # 质量分（裁判打分）不在这里做——它作为一次受控的遍历，
            # 在每一列都结束后运行（见下方），这样裁判不会收到一波并发的
            # 调用而跳过某些列。
            send("result", {"spec": spec, "provider": provider, "model": settings.model,
                            "reply": result.reply, "gate": (gate or None),
                            "iterations": result.iterations, "latency_ms": ms,
                            "tools": [{"tool": c["tool"]} for c in result.tool_calls],
                            "tokens_in": tin, "tokens_out": tout, "cost_usd": cost,
                            "completion": completion, "quality": None})
        except (Exception, SystemExit) as exc:
            # SystemExit（不是 Exception 的子类）是 get_client 在密钥缺失或
            # 配置错误时抛出的。也要抓住它，否则没有密钥的模型提供方会
            # 在对比中无声消失，而不是显示出它失败的原因。
            send("result", {"spec": spec, "provider": provider, "model": model, "error": str(exc)[:200]})

    with ThreadPoolExecutor(max_workers=min(len(specs), 6)) as ex:
        list(ex.map(run, specs))

    # 在整场对比之后打分，作为一次温和的遍历——这样裁判收到的是平稳的
    # 请求流（max_workers=2），而不是每一列完成那一刻的突发流量；突发流量
    # 曾经导致 429，留下一些没被评分的模型。每次打分都会更新对应卡片
    # （"grade" 事件）和已存结果，所以历史 + 记分板最终每个模型都有分数。
    if judge:
        jp, _, jm = (judge_spec or "").partition(":")
        gradable = [r for r in collected if not r.get("error") and (r.get("reply") or "").strip()]
        emit("grading", {"n": len(gradable), "judge": jm or judge_mod.JUDGE_MODEL})

        def grade(r):
            if r.get("error") or not (r.get("reply") or "").strip():
                return
            q = judge_mod.judge_reply(message, r["reply"], jp or None, jm or None,
                                      tools=[t.get("tool") for t in (r.get("tools") or [])])
            r["quality"] = q                       # 并入将被持久化的内容
            send("grade", {"spec": r.get("spec"), "quality": q})

        with ThreadPoolExecutor(max_workers=2) as jex:
            list(jex.map(grade, list(collected)))

    # 把这场对比持久化到竞技场自己的历史（绝不写智能体的真实状态）。
    try:
        compare_history.append_run(load_settings().home, message, collected)
    except Exception:
        pass   # 历史写入的偶然闪失绝不能让整场对比失败
    emit("done", {})


def compare_clear(payload: dict) -> dict:
    """清空对比记分板/历史（Clear 按钮）。只动竞技场自己的日志；其它一概不碰。"""
    compare_history.clear(load_settings().home)
    return {"ok": True, "runs": [], "aggregate": []}


def _compare_history_response(runs: list[dict]) -> dict:
    """用「当前」的价格表，按各自 token 为每条已存结果重新计价（这样一次
    价格修正就能校正过去的对比），再做聚合，并给每行打上费率标记。
    这是 /api/compare/history 和重评分端点返回的共享结构。"""
    for run in runs:
        for r in run.get("results", []):
            if r.get("error"):
                continue
            pin, pout = price_for(r.get("provider", ""), r.get("model", ""))
            r["cost_usd"] = round((r.get("tokens_in") or 0) / 1e6 * pin
                                  + (r.get("tokens_out") or 0) / 1e6 * pout, 4)
    agg = compare_history.aggregate(runs)
    for row in agg:
        row["rate_in"], row["rate_out"] = price_for(row["provider"], row["model"])
    return {"runs": runs[-20:][::-1], "aggregate": agg}


def compare_regrade(payload: dict) -> dict:
    """在最近一场对比上重跑裁判——用于第一次被评分器跳过（429）的模型。
    `only_missing`（默认 true）只给那些还没评分的打分；传 false 则给所有
    模型重新打分。返回刷新后的历史 + 记分板，结构与 /api/compare/history 相同。"""
    home = load_settings().home
    runs = compare_history.load_runs(home)
    if not runs:
        return {"runs": [], "aggregate": []}
    jp, _, jm = (payload.get("judge_model") or "").partition(":")
    only_missing = payload.get("only_missing", True)
    spec = payload.get("spec")   # 只给某一张卡打分（每卡一个按钮）
    last = runs[-1]
    for r in last.get("results", []):
        if r.get("error") or not (r.get("reply") or "").strip():
            continue
        if spec is not None and r.get("spec") != spec:
            continue
        if spec is None and only_missing and r.get("quality") is not None:
            continue
        q = judge_mod.judge_reply(last.get("message", ""), r["reply"], jp or None, jm or None,
                                  tools=r.get("tools"))   # 历史里存的 tools 是 [名称列表]
        if q is not None:
            r["quality"] = q
    compare_history.save_runs(home, runs)
    return _compare_history_response(runs)


def compare_delete_run(payload: dict) -> dict:
    """从记分板删除一场对比（按时间戳）——它的模型从合计里退出——其余每场
    对比保持原样。返回刷新后的历史。"""
    home = load_settings().home
    ts = payload.get("ts")
    runs = [r for r in compare_history.load_runs(home) if r.get("ts") != ts]
    compare_history.save_runs(home, runs)
    return _compare_history_response(runs)


# 粗略的每百万 token 美元价（进 / 出），用于美元「估算」——这是人真正
# 有感知的数字。按模型提供方索引；刻意取近似值，并标注为 "est"。
PRICING = {
    "anthropic": (3.0, 15.0), "openai": (2.5, 15.0), "gemini": (0.3, 2.5),
    "deepseek": (0.435, 0.87), "minimax": (0.30, 1.20), "kimi": (0.6, 2.5), "glm": (0.6, 2.2),
    "xai": (3.0, 15.0),   # Grok——粗略估算；配置了密钥的用户从目录拿到精确价
    # 当实时目录不可达时，openrouter 对有付费模型使用的兜底价
    # （目录中段的粗略估计）。":free" id 和目录已定价的模型永远不会
    # 走到这里：见 price_for()。
    "openrouter": (1.0, 3.0),
}

# 模型 id -> 精确价（每 M 进 / 每 M 出），由 list_models() 里的实时目录
# 抓取填充。OpenRouter 按模型报告价格，所以成本估算可以每次调用都精确，
# 而不是每个模型提供方一个数。
_price_cache: dict[str, tuple[float, float]] = {}


# 针对「没有可枚举目录」的端点记录的已知按模型价格（每 M 进 / 出）
# （anthropic 线没有 /models），在模型提供方级兜底之前先查这里。
# 在同一提供方内部，模型之间的差异很大——fable-5 大约是 opus 的 2 倍，
# gemini-flash 比 gemini-pro 便宜——所以按*模型*定价才是唯一诚实的做法；
# 按提供方猜测曾让 fable-5 看起来比 opus 便宜。这些费率是标准短上下文
# 牌价（未建模缓存/批处理折扣），已于 2026 年 7 月对照各家定价页核实。
# 见 docs/benchmarks.md。
MODEL_PRICING = {
    # Anthropic —— platform.claude.com/docs/.../pricing
    "claude-opus-4-8": (5.0, 25.0),
    "claude-fable-5": (10.0, 50.0),            # Mythos 级旗舰，约为 opus 的 2 倍
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    # OpenAI —— openai.com 定价（Sol = 旗舰；chat-latest = 非推理）
    "gpt-5.6-sol": (5.0, 30.0),
    "gpt-5.3-chat-latest": (1.75, 14.0),
    # Google Gemini —— ai.google.dev 定价（标准 <200k 档）
    "gemini-3.1-pro-preview": (2.0, 12.0),
    "gemini-3.5-flash": (1.5, 9.0),
    # Moonshot Kimi —— platform.kimi.ai（highspeed = 标准 k2.7 费率的 2 倍）
    "kimi-k3": (3.0, 15.0),
    "kimi-k2.7-code-highspeed": (1.9, 8.0),
    "kimi-k2.7": (0.95, 4.0),
    # xAI Grok —— docs.x.ai/developers/pricing
    "grok-4.5": (2.0, 6.0),
    "grok-4.3": (1.25, 2.5),
}


def price_for(provider: str, model: str) -> tuple[float, float]:
    """一次调用的每 M token 价格（进 / 出）：已知时用目录的按模型价，
    ":free" id 用 $0，已知的 MODEL_PRICING id 用其价格，否则退回
    模型提供方级的 PRICING 估算。"""
    if model in _price_cache:                        # 优先用目录抓到的精确价（缓存）
        return _price_cache[model]
    if model.endswith(":free"):                      # 免费模型零成本
        return (0.0, 0.0)
    if model in MODEL_PRICING:                       # 其次查已知的按模型价
        return MODEL_PRICING[model]
    return PRICING.get(provider, (3.0, 15.0))        # 兜底：按模型提供方估算（anthropic 默认）


def usage_summary(home) -> dict:
    """读取永久的支出台账（usage.jsonl）→ 累计 token + 美元成本，外加按天、
    按模型提供方的分解。成本由 token 结合 PRICING 推导（近似，标注 'est'）。
    这份台账在演示重置后仍然存在，所以这个数字是真实的持续合计——
    可信，而不是单次会话的猜测。"""
    recs = []
    path = home / "usage.jsonl"
    if path.exists():                    # 台账不存在 = 还没有任何调用
        for line in path.read_text().splitlines():
            try:
                recs.append(json.loads(line))   # 每行一次调用的记录
            except json.JSONDecodeError:
                pass                     # 坏行跳过

    def cost(r) -> float:
        # 台账里存的是 token + 模型提供方/模型，所以旧行也能重新计价
        pin, pout = price_for(r.get("provider", ""), r.get("model", ""))
        return r.get("in", 0) / 1e6 * pin + r.get("out", 0) / 1e6 * pout

    def add(bucket, key, extra):
        b = bucket.setdefault(key, {**extra, "calls": 0, "in": 0, "out": 0, "cost": 0.0})  # 取或建桶
        b["calls"] += 1                     # 调用次数 +1
        b["in"] += r.get("in", 0)           # 累加输入 token
        b["out"] += r.get("out", 0)         # 累加输出 token
        b["cost"] += cost(r)                # 累加美元成本

    by_day, by_provider = {}, {}
    for r in recs:
        day = (r.get("ts") or "")[:10]      # 时间戳前 10 位就是日期
        add(by_day, day, {"date": day})     # 按天分组
        add(by_provider, r.get("provider", "?"), {"provider": r.get("provider", "?")})  # 按提供方分组

    return {
        "calls": len(recs),
        "total_in": sum(r.get("in", 0) for r in recs),
        "total_out": sum(r.get("out", 0) for r in recs),
        "total_cost": round(sum(cost(r) for r in recs), 4),
        "by_day": sorted(by_day.values(), key=lambda x: x["date"], reverse=True)[:30],   # 最近 30 天
        "by_provider": sorted(by_provider.values(), key=lambda x: -x["cost"]),           # 花费降序
    }


def _parse_ts(ts: str):
    try:
        return datetime.fromisoformat(ts)   # 解析 ISO 时间戳
    except (ValueError, TypeError):
        return None                         # 解析失败返回 None（调用方按「无时间」处理）


def _tool_status(output: str) -> str:
    """为 UI 给工具结果分类：ok / warn / error——只依据输出字符串
    （工具本来就如实报告，所以相信它们的话）。"""
    low = (output or "").lower()                     # 归一化小写便于匹配
    if "failed" in low or "timed out" in low or low.startswith("error"):   # 明确的失败信号
        return "error"
    if "already exists" in low or "not synced" in low or "skipped" in low:  # 非致命警告
        return "warn"
    return "ok"                                     # 其它一律视为成功


# Notion 支撑的情景记忆住在网络上，所以客户端「和」结果都用短 TTL 缓存——
# collect() 在每次 dashboard 自动刷新时运行，绝不能每隔几秒就往返 Notion
# 一次（限流 + 延迟）。sqlite 路径是本地查询，不需要这样。
_NOTION_EPISODES_TTL = 30.0   # 秒；页面大约每 5 秒轮询一次
_notion_lock = threading.Lock()
_notion_store = None                       # 只构建一次（其构造函数会调用 Notion）
_notion_episodes: tuple[float, list] | None = None   # (抓取时间, 条目)


def _get_notion_store():
    """整个 dashboard 进程唯一的一个 NotionEpisodeStore。它的构造函数要
    往返 Notion（解析数据源），所以懒加载并缓存。调用方必须持有 _notion_lock。"""
    global _notion_store
    if _notion_store is None:
        from waku.memory.episodic.notion_store import NotionEpisodeStore

        _notion_store = NotionEpisodeStore()
    return _notion_store


def collect() -> dict:
    """页面展示的所有内容，一个 JSON 块。"""
    settings = load_settings()
    settings.ensure_home()
    home = settings.home
    conn = connect(home)

    def rows(sql: str) -> list[dict]:
        return [dict(r) for r in conn.execute(sql).fetchall()]   # 执行 SQL 并把行转成 dict

    def episodes_payload() -> dict:
        """来自当前后端的情景记忆：sqlite（默认）或 notion。
        Notion 宕机绝不能拖垮整个 dashboard 的数据负载。"""
        if settings.episodic_store != "notion":
            return {
                "source": "sqlite",
                "error": "",
                "items": rows(
                    "SELECT id, happened_at, summary FROM episodes ORDER BY happened_at DESC"
                ),
            }
        try:
            global _notion_episodes
            with _notion_lock:
                store = _get_notion_store()
                if _notion_episodes and time.time() - _notion_episodes[0] < _NOTION_EPISODES_TTL:
                    return {"source": "notion", "error": "", "items": _notion_episodes[1]}
                items = store.list()
                _notion_episodes = (time.time(), items)
                return {"source": "notion", "error": "", "items": items}
        except Exception as exc:
            # 优雅降级：绝不让数据负载挂掉；如果有上一次的抓取结果就提供它
            # （一次宕机不应该让标签页空白）。
            stale = _notion_episodes[1] if _notion_episodes else []
            return {"source": "notion", "error": str(exc), "items": stale}

    episodes_data = episodes_payload()

    # --- 追踪 → 对话轮次（把 turn_start 和 turn_end 之间的事件归组）
    events = []
    trace_errors = []
    trace_files = sorted((home / "traces").glob("*.jsonl"))
    for path in trace_files:
        try:
            lines = list(iter_trace_lines(path))
        except TraceEncodingError as exc:
            trace_errors.append({"file": path.name, "error": str(exc)})
            continue
        for line in lines:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    turns, current, wake_scans = [], None, []   # 已完成轮次 / 正在组装的轮次 / 唤醒扫描
    for ev in events:                            # 把无序事件按 turn_start...turn_end 归组
        kind = ev.get("type")
        if kind == "turn_start":                 # 开新轮次：建一个空骨架
            current = {"user_message": ev.get("user_message"), "ts": ev.get("ts"),
                       "gate": None, "llm_calls": [], "tools": [], "reply": None}
        elif kind == "wake_scan":                # 唤醒扫描独立收集（不在轮次内）
            wake_scans.append(ev)
        elif current is not None:                # 非轮次边界事件都归入当前轮次
            if kind == "gate":
                current["gate"] = ev
            elif kind == "llm":
                current["llm_calls"].append(ev)
            elif kind == "tool":
                current["tools"].append(ev)
            elif kind == "consolidation":
                current["consolidation"] = ev
            elif kind == "turn_end":             # 轮次收尾：落定并追加
                current["reply"] = ev.get("reply")
                current["iterations"] = ev.get("iterations")
                turns.append(current)
                current = None                   # 清空，等下一轮
    if current is not None:  # 一场从未结束的对话轮次 = 卡死的确凿证据
        current["reply"] = "TURN NEVER FINISHED — check for a hang after this point"
        current["unfinished"] = True
        turns.append(current)                    # 保留它，让「卡死」在 UI 里可见

    # --- 推导每轮延迟 + 美元成本（人真正有感知的运维数字）
    if settings.base_url or settings.provider == "openrouter":
        list_models()  # 预热每模型价格缓存（5 分钟缓存的抓取）
    price_in, price_out = price_for(settings.provider, settings.model or "")
    for t in turns:
        start, end = _parse_ts(t["ts"]), None           # 轮次开始时间；先假定没有结束
        last = t["llm_calls"][-1]["ts"] if t["llm_calls"] else None   # 最后一轮 LLM 调用时间
        end = _parse_ts(last)                           # 作为该轮实际结束点
        t["latency_ms"] = int((end - start).total_seconds() * 1000) if start and end else None  # 起止差
        tin = sum(c.get("usage", {}).get("in", 0) for c in t["llm_calls"])   # 累计输入 token
        tout = sum(c.get("usage", {}).get("out", 0) for c in t["llm_calls"]) # 累计输出 token
        t["cost"] = tin / 1e6 * price_in + tout / 1e6 * price_out   # token × 单价
        for x in t["tools"]:
            x["status"] = _tool_status(x.get("output", ""))        # 结果分类
            x["summary"] = (x.get("output", "") or "").split(". ")[0][:120]  # 一句话摘要

    latencies = sorted(t["latency_ms"] for t in turns if t["latency_ms"] is not None)  # 有效延迟升序
    total_cost = sum(t["cost"] for t in turns)          # 全部轮次成本合计

    def pct(p: float) -> int:
        return latencies[min(len(latencies) - 1, int(len(latencies) * p))] if latencies else 0
        # 取升序列表里第 p 百分位的位置；没有数据时返回 0

    from waku.memory.procedural.loader import SkillLoader
    from waku.memory import REPO_SKILLS

    skills = [{"name": s.name, "description": s.description, "body": s.body,
               "path": str(s.path),
               # 相对路径（用于 reveal）+ 它是否住在可编辑的 home 目录里
               "rel": _rel_to_home(s.path, home),
               "editable": str((home / "skills").resolve()) in str(s.path.resolve())}
              for s in SkillLoader([REPO_SKILLS, home / "skills"]).skills]

    eval_report = None
    report_path = home / "eval_report.json"
    if report_path.exists():
        eval_report = json.loads(report_path.read_text())

    eval_history = []
    hist_path = home / "eval_runs.jsonl"
    if hist_path.exists():
        for line in hist_path.read_text().splitlines()[-20:]:
            try:
                eval_history.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    eval_history.reverse()

    outbox = [{"name": p.name, "text": p.read_text()[:400]}
              for p in sorted((home / "outbox").glob("*.txt"), reverse=True)[:20]]

    # --- state.db 内省：真实的 SQLite 表，让持久化层可见（不只是内容）。
    # 表名是硬编码的，所以 f-string 里的 SQL 是安全的。
    def table_info(name):
        info = conn.execute(f"PRAGMA table_info({name})").fetchall()   # 取表的列定义
        cols = [r["name"] for r in info]         # 列名列表
        types = {r["name"]: r["type"] for r in info}   # 列名 -> SQLite 类型
        count = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]   # 总行数
        # 最多取最新的 200 行，这样每张表都有自己的可滚动标签页
        sample = [dict(r) for r in conn.execute(f"SELECT * FROM {name} ORDER BY rowid DESC LIMIT 200").fetchall()]
        return {"name": name, "columns": cols, "types": types, "count": count, "sample": sample}

    db_path = home / "state.db"
    all_tables = [r["name"] for r in
                  conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
    db_info = {
        "path": str(db_path.resolve()),
        "size": db_path.stat().st_size if db_path.exists() else 0,
        "tables": [table_info(n) for n in ("calendar_events", "facts", "episodes", "chat_log")],
        "fts": [t for t in all_tables if t.endswith("_fts")],
        "all_tables": all_tables,
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "home": str(home.resolve()),
        "provider": settings.provider,
        "model": settings_info()["model"],
        "stats": {
            "turns": len(turns),
            "tool_calls": sum(len(t["tools"]) for t in turns),
            "tool_errors": sum(1 for t in turns for x in t["tools"] if x["status"] == "error"),
            "gate_skips": sum(1 for t in turns if t["gate"] and t["gate"].get("decision") == "skip"),
            "gate_retrieves": sum(1 for t in turns if t["gate"] and t["gate"].get("decision") == "retrieve"),
            "tokens_in": sum(c.get("usage", {}).get("in", 0) for t in turns for c in t["llm_calls"]),
            "tokens_out": sum(c.get("usage", {}).get("out", 0) for t in turns for c in t["llm_calls"]),
            "cost": round(total_cost, 4),
            "latency_avg": int(sum(latencies) / len(latencies)) if latencies else 0,
            "latency_p95": pct(0.95),
            "trace_files": len(trace_files),
        },
        "turns": turns[::-1][:50],
        "wake_scans": wake_scans[::-1][:25],
        # 最后的原始追踪行，让运维标签页内联展示追踪（不需要打开文件夹）
        "trace_tail": [{"type": e.get("type"), "ts": e.get("ts"),
                        "detail": (e.get("user_message") or e.get("decision") or e.get("tool")
                                   or e.get("reply") or "")}
                       for e in events[-18:]][::-1],
        "trace_file": (trace_files[-1].name if trace_files else None),
        "trace_errors": trace_errors,
        "facts": rows("SELECT id, subject, content, source, created_at FROM facts ORDER BY id DESC"),
        "episodes": episodes_data["items"],
        "episodes_source": episodes_data["source"],
        "episodes_error": episodes_data["error"],
        "soul": (home / "SOUL.md").read_text() if (home / "SOUL.md").exists() else "",
        "chat_pending": conn.execute("SELECT COUNT(*) FROM chat_log WHERE consolidated=0").fetchone()[0],
        "chat_log": rows("SELECT role, content, consolidated, source, session_id, created_at FROM chat_log ORDER BY id DESC LIMIT 80")[::-1],
        "sessions": session_list(conn),
        "current_session": (_agent.session.session_id if _agent is not None else _dash_session()),
        "consolidate_every": settings.consolidate_every,
        "calendar": rows('SELECT title, start, "end", attendees, created_at FROM calendar_events ORDER BY start'),
        "outbox": outbox,
        "skills": skills,
        "eval_report": eval_report,
        "eval_history": eval_history,
        "db": db_info,
        "settings": settings_info(),
        "tools": tools_info(),
        "usage": usage_summary(home),
    }


def _rel_to_home(path, home) -> str:
    """如果路径住在 WAKU_HOME 里则返回相对 WAKU_HOME 的路径，否则返回
    相对仓库的 'skills/...' 路径——两条路都是 reveal_path 能打开的。"""
    try:
        return str(path.resolve().relative_to(home.resolve()))
    except ValueError:
        return str(path)


def session_list(conn) -> list[dict]:
    """聊天历史选择器里每个对话一行：id、它的第一条用户消息（作为标题）、
    消息数，最新的在前。会话只是 chat_log 行上的一个 session_id 标签——
    同一张表，没有新的存储。"""
    groups = conn.execute(
        """SELECT session_id, COUNT(*) AS messages, MAX(created_at) AS last_at
           FROM chat_log GROUP BY session_id ORDER BY last_at DESC"""
    ).fetchall()
    out = []
    for g in groups:
        sid = g["session_id"]
        first = conn.execute(
            "SELECT content FROM chat_log WHERE session_id=? AND role='user' ORDER BY id LIMIT 1",
            (sid,),
        ).fetchone()
        last = conn.execute(
            "SELECT role, content FROM chat_log WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,)
        ).fetchone()
        sources = [r["source"] for r in conn.execute(
            "SELECT DISTINCT source FROM chat_log WHERE session_id=?", (sid,)).fetchall()]
        preview = ""
        if last:
            preview = ("you: " if last["role"] == "user" else "waku: ") + last["content"][:80]
        out.append({"id": sid,
                    "title": (first["content"][:60] if first else "(empty)"),
                    "last": preview,
                    "sources": sources,
                    "messages": g["messages"],
                    "last_at": g["last_at"]})
    return out


# 工具的来源，用于「工具」标签页里的分组（名称 → 类别）。
_FLAGSHIP = {"create_event", "list_events", "save_note", "send_message"}
_SELFMGMT = {"manage_memory", "update_soul", "create_skill"}
_APPLE = {"read_apple_calendar", "read_apple_mail", "create_reminder", "create_note"}
_WEB = {"search_web"}


def _tool_source(name: str, mcp_servers: list[str]) -> str:
    if name in _FLAGSHIP:
        return "flagship"
    if name in _WEB:
        return "web"
    if name in _SELFMGMT:
        return "self-management"
    if name in _APPLE:
        return "apple"
    if any(name.startswith(f"{s}_") for s in mcp_servers):
        return "mcp"
    return "other"


def tools_info() -> dict:
    """智能体可用的工具 + 任何配置好的 MCP 服务器——这样「工具」标签页展示的是
    「能力」，而不只是工具调用产生的产物。当存在运行中的智能体时，反映它真实的
    注册表（精确）；否则构建一个仅供展示的目录（不会仅为渲染页面而启动
    MCP 子进程）。"""
    settings = load_settings()
    settings.ensure_home()
    mcp = {"configured": False, "servers": [], "live": False}
    mcp_path = settings.home / "mcp.json"
    if mcp_path.exists():
        mcp["configured"] = True
        try:
            mcp["servers"] = [s.get("name", "?") for s in json.loads(mcp_path.read_text()).get("servers", [])]
        except (json.JSONDecodeError, OSError):
            pass

    catalog = []
    if _agent is not None:
        mcp["live"] = getattr(_agent, "mcp_bridge", None) is not None
        tools = list(_agent.tools._tools.values())
    else:
        # 仅供展示：同样的工具但去掉 MCP（构建真实注册表会启动 MCP 服务器，
        # 5 秒一次的轮询可不想这样）。
        from waku.memory import Memory
        from waku.tools import calendar, memory_admin, messages, notes, search

        conn = connect(settings.home)
        try:
            # Notion 模式：复用 dashboard 那一个缓存的客户端，而不是让
            # Memory() 每次轮询都新建一个（issue #20）。
            episode_store = None
            if settings.episodic_store == "notion":
                with _notion_lock:
                    episode_store = _get_notion_store()
            mem = Memory(conn, settings, None, episode_store=episode_store)
        except Exception:
            # 配置错误的可选后端（notion/supabase）绝不能拖垮 dashboard——
            # 改为从仅供展示的目录里去掉记忆管理工具。
            mem = None
        tools = [calendar.make_tool(conn, settings.home, apple_calendar=settings.apple_calendar),
                 calendar.make_list_tool(conn),
                 notes.make_tool(conn), messages.make_tool(settings.home),
                 search.make_tool(),
                 memory_admin.make_update_soul_tool(settings)]
        if mem is not None:
            tools += [memory_admin.make_manage_memory_tool(mem),
                      memory_admin.make_create_skill_tool(settings, mem)]
        if settings.apple_tools:
            from waku.tools import apple

            tools += apple.make_tools()
    for t in tools:
        catalog.append({"name": t.name, "description": t.description,
                        "source": _tool_source(t.name, mcp["servers"])})
    catalog.sort(key=lambda c: (c["source"], c["name"]))
    from waku.tools.experimental import PLANNED

    return {"catalog": catalog, "mcp": mcp, "apple_on": settings.apple_tools,
            "planned": PLANNED}   # 白板上的方框还没接线（即将推出）


def run_query(payload: dict) -> dict:
    """一个极小的只读 SQL 控制台（Supabase 编辑器的想法，做了缩小）。
    以只读模式打开 state.db，让写入无法溜进来，并且只接受单条
    SELECT/WITH 语句。最多 200 行。"""
    sql = (payload.get("sql") or "").strip().rstrip(";").strip()   # 去空白并剥掉末尾分号
    if not sql:
        return {"error": "Type a SELECT query."}
    low = sql.lower()
    if not (low.startswith("select") or low.startswith("with")):   # 只放行只读语句
        return {"error": "Only SELECT (or WITH … SELECT) queries are allowed."}
    if ";" in sql:                               # 拒绝多条语句，防止注入式的拼接
        return {"error": "One statement at a time (no semicolons)."}
    import sqlite3

    settings = load_settings()
    settings.ensure_home()
    db = (settings.home / "state.db").resolve()
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)   # 只读模式打开，杜绝写入
        c.row_factory = sqlite3.Row             # 让行可用名字取字段
        cur = c.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []   # 列名
        data = [[str(r[i]) if r[i] is not None else "" for i in range(len(cols))]
                for r in cur.fetchmany(200)]    # 最多取 200 行，全部转成字符串
        c.close()
        return {"columns": cols, "rows": data}
    except sqlite3.Error as exc:
        return {"error": str(exc)}              # 把 SQL 错误原文浮给前端


_whisper = None
_whisper_lock = threading.Lock()


def transcribe_audio(raw: bytes) -> dict:
    """dashboard 麦克风按钮的服务器端语音转文字——用同一个本地 Whisper
    （`make voice` 也是用它），所以浏览器里不用任何云端就能语音。
    需要 [voice] 可选依赖。返回 {text} 或友好的 {error}。"""
    if not raw:
        return {"error": "no audio received"}
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return {"error": "voice isn't installed — run: pip install -e '.[voice]'"}
    global _whisper
    import os as _os
    import tempfile

    with _whisper_lock:
        if _whisper is None:
            _whisper = WhisperModel(os.getenv("WAKU_WHISPER_MODEL", "base"), compute_type="int8")
    # 浏览器发送的是 WAV（PCM）——Whisper/PyAV 能可靠解码（MediaRecorder
    # 产出的 WebM/Opus 常常解码失败）。
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.write(raw)
    tmp.close()
    try:
        segments, _ = _whisper.transcribe(tmp.name)
        return {"text": " ".join(s.text for s in segments).strip()}
    except Exception as exc:
        return {"error": f"transcription failed: {exc}"}
    finally:
        try:
            _os.unlink(tmp.name)
        except OSError:
            pass


def _thread_history(conn, sid: str) -> list[dict]:
    """聊天停靠区加载一条线程的「唯一」路径：role + content + 每轮元数据
    （门禁/统计/工具/模型），这样每张卡片都能完整渲染。
    id '__all__' 返回整个跨线程时间线（像循环标签页那样，但以聊天形式）。
    所有加载历史的路径都经过这里，这样它们就不会分叉
    （以前分叉过：'switch' 丢掉元数据、只显示文本）。"""
    if sid == "__all__":
        rows = conn.execute(
            "SELECT role, content, meta FROM chat_log ORDER BY id DESC LIMIT 200"
        ).fetchall()[::-1]
    else:
        rows = conn.execute(
            "SELECT role, content, meta FROM chat_log WHERE session_id=? ORDER BY id",
            (sid,),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"],
             "meta": json.loads(r["meta"]) if r["meta"] else None} for r in rows]


def session_action(payload: dict) -> dict:
    """聊天历史控制：开始新对话、切换到过去的对话，或读取某段对话的历史
    （只读，用于实时收件箱）。会话活在 chat_log 里。"""
    action = payload.get("action")
    if action == "history":
        # 对话的只读视图——绝不动智能体，所以 dashboard 能实时轮询它
        # （例如展示新的 Telegram 消息到达）。
        settings = load_settings()
        settings.ensure_home()
        conn = connect(settings.home)
        sid = payload.get("id") or "default"
        return {"ok": True, "session_id": sid, "history": _thread_history(conn, sid)}
    with _agent_lock:
        agent = _get_agent()
        if action == "new":
            sid = datetime.now().strftime("s-%Y%m%d-%H%M%S")
            agent.session.start_new(sid)
            return {"ok": True, "session_id": sid, "history": []}
        if action == "switch":
            sid = payload.get("id") or "default"
            agent.session.switch(sid)
            # 与只读的 "history" 动作相同的、带丰富元数据的行，这样切换后的
            # 线程也能渲染出完整的对话轮次卡片（门禁/统计/工具/模型）——
            # 而不只是文本。（这两条路径以前不一致。）
            return {"ok": True, "session_id": sid, "history": _thread_history(agent.conn, sid)}
    return {"error": f"unknown action {action}"}


def _editor_cmd() -> list[str] | None:
    """用户的代码编辑器命令行：$WAKU_EDITOR，然后 cursor，然后 code。"""
    import shutil

    custom = os.getenv("WAKU_EDITOR")
    if custom and shutil.which(custom):
        return [custom]
    for cli in ("cursor", "code"):
        if shutil.which(cli):
            return [cli]
    return None


def reveal_path(rel: str) -> dict:
    """打开 WAKU_HOME 下的一个文件/文件夹——如果 PATH 上有代码编辑器
    （cursor/code/$WAKU_EDITOR）就用它打开，否则在 Finder 里 reveal。
    限制为 WAKU_HOME 内部的路径。"""
    import subprocess
    import sys

    settings = load_settings()
    settings.ensure_home()
    home = settings.home.resolve()
    target = (home / (rel or ".")).resolve()
    if target != home and home not in target.parents:
        return {"error": "path is outside the .waku home"}
    if not target.exists():
        return {"error": f"not found: {target}"}

    editor = _editor_cmd()
    if editor and target.is_file() and target.suffix != ".db":  # 编辑器处理不了 sqlite
        subprocess.run([*editor, str(target)], check=False)
        return {"ok": True, "opened_in": editor[0], "path": str(target)}
    if sys.platform != "darwin":
        return {"error": f"no editor found and reveal is macOS-only — the path is {target}"}
    subprocess.run(
        ["open", "-R", str(target)] if target.is_file() else ["open", str(target)],
        check=False,
    )
    return {"ok": True, "revealed": str(target)}


def memory_action(payload: dict) -> dict:
    """从 dashboard 对记忆做人工增删改：更新/删除事实与情景、改写 SOUL.md。
    写入智能体用的同一个 sqlite 文件（busy_timeout 处理并发争用）；
    改动对智能体的下一轮立即生效。"""
    from waku.memory.episodic.store import SqliteEpisodeStore
    from waku.memory.semantic.store import SqliteFactStore

    settings = load_settings()
    settings.ensure_home()
    action = payload.get("action")
    if action == "save_soul":
        text = (payload.get("content") or "").strip()
        if not text:
            return {"error": "SOUL cannot be empty"}
        (settings.home / "SOUL.md").write_text(text + "\n")
        return {"ok": True}
    if action == "save_skill":
        # 手工编辑任意已加载的 SKILL.md（与智能体的 create_skill 写入的
        # 是同一个文件）——仓库技能和 home 技能都一样。限定在「两个技能
        # 文件夹」的沙箱里；写入前校验 frontmatter。
        from pathlib import Path

        from waku.memory import REPO_SKILLS
        from waku.memory.procedural.loader import _parse_text

        text = (payload.get("content") or "").strip()
        dest = Path(payload.get("path") or "").resolve()
        allowed = [REPO_SKILLS.resolve(), (settings.home / "skills").resolve()]
        if dest.name != "SKILL.md" or not any(a in dest.parents for a in allowed):
            return {"error": "can only edit SKILL.md files inside the skills folders"}
        if _parse_text(text, dest) is None:
            return {"error": "invalid SKILL.md — needs a name and description in the frontmatter"}
        dest.write_text(text.rstrip() + "\n", encoding="utf-8")
        return {"ok": True}

    conn = connect(settings.home)
    facts, episodes = SqliteFactStore(conn), SqliteEpisodeStore(conn)
    if action == "delete_episode" and settings.episodic_store == "notion":
        global _notion_episodes
        with _notion_lock:
            ok = _get_notion_store().delete(str(payload.get("id", "")))
            # 让 TTL 缓存失效，这样下一次 collect() 会重新抓取——否则一条
            # 已删除的情景会在页面上残留最多 30 秒
            _notion_episodes = None
        return {"ok": ok}
    try:
        rid = int(payload.get("id", 0))
    except (TypeError, ValueError):
        return {"error": "bad id"}
    if action == "update_fact":
        return {"ok": facts.update(rid, payload.get("content", ""), payload.get("subject") or None)}
    if action == "delete_fact":
        return {"ok": facts.delete(rid)}
    if action == "delete_episode":
        return {"ok": episodes.delete(rid)}
    return {"error": f"unknown action {action}"}


_models_cache: dict[str, tuple[float, list]] = {}


def _known_default_ids(prov, out: dict, is_active: bool) -> list[dict]:
    """实时目录不可达时的尽力而为模型列表：该模型提供方的旗舰 + 快速 +
    循环/门禁默认值——这样展示用的模型（如 opus-4.8）也会被提供，而不只是
    循环的两个默认值——如果这是当前模型提供方，还加上当前激活的模型。"""
    ids = [*(prov.default_pair() if prov else []),
           prov.model if prov else "", prov.small_model if prov else ""]
    if is_active:
        ids = [out.get("model"), out.get("small_model"), *ids]
    return [{"id": m} for m in dict.fromkeys(m for m in ids if m)]


def list_models(provider: str | None = None) -> dict:
    """某个模型提供方可用的模型 id，供设置里的模型选择器使用——这些默认值
    只是起点，绝不是菜单的全部。传入 `provider` 可以列出「任意」模型提供方的
    目录（「你的模型」新增行会先选提供方）；不传则使用「当前」模型提供方。
    三个来源：显式的 Provider.catalog_url（anthropic、kimi）、在
    OpenAI 兼容端点上的 GET {base_url}/models（OpenRouter、Gemini、任意
    WAKU_BASE_URL），或没有目录时的那两个已知默认值。OpenRouter 条目带有
    free / 工具支持 / 上下文元数据，让选择器能浮出那些 $0 且支持工具的模型。
    缓存 5 分钟。"""
    import time
    import urllib.request

    from waku.loop.models import PROVIDERS

    s = load_settings()
    # 显式传入的模型提供方会覆盖当前激活的（以及它自定义的 base_url：
    # WAKU_BASE_URL 只作用于为它设置的那个模型提供方）。
    name = provider or s.provider
    prov = PROVIDERS.get(name)
    base = (s.base_url if name == s.provider else None) or (prov.base_url if prov else None)
    out = {
        "provider": name,
        "model": s.model or (prov.model if prov else ""),
        "small_model": s.small_model or (prov.small_model if prov else ""),
        "endpoint": base or name,
    }
    # 这个模型提供方的模型能在哪里列出？显式的 catalog_url 优先
    # （kimi 走 anthropic 线聊天，但在它自己的 OpenAI 兼容 API 上列模型；
    # anthropic 本身有 GET /v1/models）；否则 openai 线端点用
    # {base_url}/models；再否则退回那两个已知默认值。
    if prov is not None and prov.catalog_url:
        url = prov.catalog_url
    elif prov is not None and prov.kind == "openai" and base:
        url = base.rstrip("/") + "/models"
    else:
        # 没有目录端点：退回该模型提供方自己的已知默认值
        # （旗舰 + 快速 + 循环/门禁），而不是只有当前激活的模型。
        return {**out, "listed": False,
                "models": _known_default_ids(prov, out, name == s.provider)}

    cached = _models_cache.get(url)
    if cached and time.time() - cached[0] < 300:
        _ts, cmodels, cerr = cached          # 真实列出时 cerr 为 None
        r = {**out, "listed": cerr is None, "models": cmodels}
        if cerr:
            r["error"] = cerr
        return r
    # 用该模型提供方自己的密钥；s.api_key 只保存「当前」模型提供方的。
    key = ((s.api_key if name == s.provider else "") or os.getenv(prov.key_env, "")).strip()
    # HTTP 头必须是 latin-1；一个带有零散非 ASCII 字符的密钥（智能箭头/引号，
    # 或糟糕粘贴带来的换行）会用一个晦涩的编解码错误让整个列表崩溃，
    # 并悄悄退回两个默认值。在这里抓住它，并给出一条真正说明怎么修的提示。
    try:
        key.encode("latin-1")
    except UnicodeEncodeError:
        msg = (f"{prov.key_env} contains a non-ASCII character — re-paste the key "
               f"(no spaces, line breaks, or arrows).")
        return {**out, "listed": False,
                "models": _known_default_ids(prov, out, name == s.provider), "error": msg}
    # 同时发送两种认证风格——Bearer 给 OpenAI 兼容目录，x-api-key +
    # version 给 Anthropic 的；每个服务器读取它认识的那个头
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}",
        "x-api-key": key, "anthropic-version": "2023-06-01",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        # 浮出服务器的真实原因（例如 xAI 的 403「no credits」），而不仅仅
        # 是「HTTP Error 403」——HTTPError 把响应体放在 .read() 上。
        msg = str(exc)
        try:
            msg = f"{msg} — {exc.read().decode()[:160]}"
        except Exception:
            pass
        # 仍然提供该模型提供方的已知默认值，让选择器不至于空着
        known = _known_default_ids(prov, out, name == s.provider)
        # 把失败（默认值 + 原因）缓存约 1 分钟，这样一个不可达的目录不会
        # 让每 5 秒一次的 dashboard 轮询卡 10 秒——也让缓存命中时仍能显示
        # 默认值和原因，而不是空白列表。
        _models_cache[url] = (time.time() - 240, known, msg)
        return {**out, "listed": False, "models": known, "error": msg}
    models = []
    for m in data.get("data", []):
        mid = m.get("id", "")
        if not mid:
            continue
        pricing = m.get("pricing") or {}
        params = m.get("supported_parameters")
        entry = {
            "id": mid,
            "free": mid.endswith(":free") or pricing.get("prompt") == "0",
            # None 表示端点没有说明（只有 OpenRouter 会报告这个）
            "tools": ("tools" in params) if params is not None else None,
            # 推理模型会边「想」边花 token，这会让门禁那点小预算爆掉：
            # UI 引导它们避开门禁槽位
            "reasoning": ("reasoning" in params) if params is not None else None,
            "context": m.get("context_length"),
        }
        try:
            # OpenRouter 的价格是 $/token 字符串；换算成 $/M 用于展示 + 成本
            pin, pout = float(pricing["prompt"]) * 1e6, float(pricing["completion"]) * 1e6
            _price_cache[mid] = (pin, pout)
            entry["price_in"], entry["price_out"] = round(pin, 3), round(pout, 3)
        except (KeyError, TypeError, ValueError):
            pass
        models.append(entry)
    models.sort(key=lambda x: (not x["free"], x["tools"] is False, x["id"]))
    _models_cache[url] = (time.time(), models, None)   # error 为 None = 真实列出
    return {**out, "listed": True, "models": models}


def _models_json() -> Path:
    return load_settings().home / "models.json"


def default_pinned_specs() -> list[str]:
    """在用户打理出自己的列表之前的起步候选：每个设置了密钥的模型提供方的
    旗舰 + 快速（这样切换器只显示你真正能用的模型）。旗舰排最前，
    所以它就是那个模型提供方的默认。"""
    from waku.loop.models import PROVIDERS

    specs = []
    for name, prov in PROVIDERS.items():
        if os.getenv(prov.key_env):
            specs += [f"{name}:{m}" for m in prov.default_pair()]
    return specs


def pinned_specs() -> list[str]:
    """用户打理的『provider:model』候选列表（有序），来自 .waku/models.json。
    聊天切换器展示的正是这些。在他们保存任何东西之前，退回旗舰+快速默认值。"""
    p = _models_json()
    if p.exists():
        try:
            return json.loads(p.read_text()).get("pinned", [])
        except (json.JSONDecodeError, OSError):
            pass
    return default_pinned_specs()


def default_model_for(provider: str) -> str:
    """某模型提供方的默认模型 = 用户为它钉选的第一个。
    空字符串表示「用该模型提供方内置的默认」。"""
    for spec in pinned_specs():
        p, _, m = spec.partition(":")
        if p == provider and m:
            return m
    return ""


def pin_action(payload: dict) -> dict:
    """管理打理的模型候选列表：钉选 / 取消钉选 / 设为默认。"""
    action = payload.get("action")
    provider, model = payload.get("provider", ""), payload.get("model", "")
    if not provider or not model:
        return {"error": "provider and model required"}
    spec = f"{provider}:{model}"
    specs = [s for s in pinned_specs() if s != spec]
    if action == "pin":
        specs.append(spec)
    elif action == "default":
        # 移到其模型提供方分组的最前面 -> 成为那个模型提供方的默认
        idx = next((i for i, s in enumerate(specs) if s.split(":", 1)[0] == provider), len(specs))
        specs.insert(idx, spec)
    elif action != "unpin":
        return {"error": f"unknown action {action}"}
    path = _models_json()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pinned": specs}, indent=1))
    return {"ok": True, **settings_info()}


def settings_info() -> dict:
    """当前的模型提供方/模型 + 哪些密钥已设置——脱敏到最后 4 位，绝不给完整
    密钥。`pinned` 是用户打理的模型候选列表（聊天切换器跨模型提供方展示
    的正是这些）。"""
    from waku.loop.models import PROVIDERS

    s = load_settings()
    prov = PROVIDERS.get(s.provider)
    # 打理的候选列表，按顺序；每个模型提供方第一个被钉选的模型就是
    # 该提供方的默认（切换提供方时用到）。
    pinned, seen = [], set()
    for spec in pinned_specs():
        p, _, m = spec.partition(":")
        if m:
            pinned.append({"provider": p, "model": m, "default": p not in seen})
            seen.add(p)
    # 按模型提供方分组展示（这样同一家实验室的模型会聚在一起，
    # 例如后加入的 claude-fable-5 会并进其它 anthropic 行）。用「稳定」排序
    # 按模型提供方首次出现的顺序排列，保持各提供方内部自己的顺序——
    # 这样它的默认（第一个被钉选的）保持在最上面，上面的 'default' 标记
    # 仍然对得上。
    prov_order: dict = {}
    for row in pinned:
        prov_order.setdefault(row["provider"], len(prov_order))
    pinned.sort(key=lambda row: prov_order[row["provider"]])
    return {
        "provider": s.provider,
        "model": s.model or (prov.model if prov else ""),
        "small_model": s.small_model or (prov.small_model if prov else ""),
        "pinned": pinned,
        # 通过 WAKU_BASE_URL / WAKU_API_KEY 设置的自定义端点（例如 OpenRouter）
        "base_url": s.base_url or "",
        "custom_key_set": bool(s.api_key),
        "providers": [
            {"name": name, "key_env": p.key_env,
             "key_set": bool(os.getenv(p.key_env)),
             "key_last4": (os.getenv(p.key_env) or "")[-4:],
             "default_model": p.model, "default_small_model": p.small_model}
            for name, p in PROVIDERS.items()
        ],
        # 可选的网络搜索密钥（Tavily）——与模型提供方密钥一样的 BYOK 处理
        "search_key_env": "TAVILY_API_KEY",
        "search_key_set": bool(os.getenv("TAVILY_API_KEY")),
        "search_key_last4": (os.getenv("TAVILY_API_KEY") or "")[-4:],
        # 情景记忆后端：sqlite（默认）或 notion
        "episodic_store": s.episodic_store,
        "notion_token_set": bool(os.getenv("NOTION_TOKEN")),
        "notion_token_last4": (os.getenv("NOTION_TOKEN") or "")[-4:],
        "notion_db_set": bool(os.getenv("NOTION_EPISODES_DATABASE_ID")),
        "notion_db_last4": (os.getenv("NOTION_EPISODES_DATABASE_ID") or "")[-4:],
    }


def apply_settings(payload: dict) -> dict:
    """写入 .env + os.environ，然后重建智能体，让切换立即生效。
    绝不记录密钥；只有白名单内的环境变量名可写。"""
    global _agent
    from dotenv import find_dotenv, set_key

    from waku.loop.models import PROVIDERS

    provider = payload.get("provider")
    if provider not in PROVIDERS:
        return {"error": f"unknown provider {provider}"}
    episodic_store = payload.get("episodic_store")
    if episodic_store is not None and episodic_store not in ("sqlite", "notion"):
        return {"error": f"unknown episodic_store {episodic_store}"}
    before = {"provider": os.getenv("WAKU_PROVIDER", ""),
              "model": os.getenv("WAKU_MODEL", ""),
              "small_model": os.getenv("WAKU_SMALL_MODEL", "")}
    writable = ({"WAKU_PROVIDER", "WAKU_MODEL", "WAKU_SMALL_MODEL", "TAVILY_API_KEY",
                 "WAKU_EPISODIC_STORE", "NOTION_TOKEN", "NOTION_EPISODES_DATABASE_ID"}
                | {p.key_env for p in PROVIDERS.values()})
    env_path = find_dotenv(usecwd=True) or ".env"

    updates = {"WAKU_PROVIDER": provider,
               "WAKU_MODEL": payload.get("model", "") or "",
               "WAKU_SMALL_MODEL": payload.get("small_model", "") or ""}
    if episodic_store:
        updates["WAKU_EPISODIC_STORE"] = episodic_store
    # 切换模型提供方时绝不把模型带跨端点（真实 bug：kimi->gemini 后门禁模型
    # 仍是 kimi-k3，每一轮都在 Gemini 上 404）。但如果用户没有新输入模型，
    # 就使用「这个」模型提供方的默认（用户为它第一个钉选的，否则用它内置
    # 的默认）——「每个 API 密钥一个默认模型」。请求体里显式的模型
    # （例如来自聊天胶囊）总是优先。
    if provider != before["provider"]:
        if updates["WAKU_MODEL"] in ("", before["model"]):
            updates["WAKU_MODEL"] = default_model_for(provider)
        if updates["WAKU_SMALL_MODEL"] in ("", before["small_model"]):
            updates["WAKU_SMALL_MODEL"] = ""
    for k, v in (payload.get("keys") or {}).items():
        if k in writable and v:  # 只有非空密钥才覆盖
            if k == "NOTION_EPISODES_DATABASE_ID":
                from waku.memory.episodic.notion_store import normalize_database_id

                try:
                    v = normalize_database_id(v)
                except ValueError as exc:
                    return {"error": str(exc)}
            updates[k] = v
    for k, v in updates.items():
        if k in writable:
            set_key(env_path, k, v)
            os.environ[k] = v

    with _agent_lock:
        old = _agent
        try:
            new_settings = load_settings()
            new_settings.ensure_home()
            conn = connect(new_settings.home, check_same_thread=False)
            from waku.app import Waku

            _agent = Waku(settings=new_settings, conn=conn)
        except (Exception, SystemExit) as exc:  # get_client 会抛 SystemExit
            _agent = old
            return {"error": str(exc)}
    if old is not None:
        old.close()
    # 模型/模型提供方切换是一次 RELEASE 事件（白板上的「new model config」
    # 方框）——把它记进追踪，让「换大脑」可审计
    _agent.tracer.event("config", {
        "from": before,
        "to": {"provider": provider, "model": updates["WAKU_MODEL"],
               "small_model": updates["WAKU_SMALL_MODEL"]},
    })
    return {"ok": True, **settings_info()}


def events_since(cursor):
    """`cursor`（今天追踪文件的行数）之后的新追踪事件。任意网关——浏览器、
    CLI、voice、Telegram——都往这同一个文件里追加，所以实时架构图能为
    它们全部点亮。cursor=None 只返回当前的尾部，让浏览器从新状态开始，
    而不是回放历史。"""
    settings = load_settings()
    settings.ensure_home()
    path = settings.home / "traces" / (datetime.now().strftime("%Y-%m-%d") + ".jsonl")
    if not path.exists():
        return {"events": [], "cursor": 0}
    try:
        lines = list(iter_trace_lines(path))
    except TraceEncodingError as exc:
        return {"events": [], "cursor": 0, "error": str(exc)}
    if cursor is None or cursor < 0 or cursor > len(lines):
        return {"events": [], "cursor": len(lines)}
    out = []
    for ln in lines[cursor:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            pass
    return {"events": out, "cursor": len(lines)}


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str, *, no_cache: bool = False) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # 前端文件（app.js/style.css）在开发过程中会变；没有这个头，
        # 浏览器会提供过期的缓存副本，改动看起来像「丢失」了。
        if no_cache:
            self.send_header("Cache-Control", "no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 — http.server 的 API
        if self.path == "/api/data":                     # 概览页主数据块
            self._send(json.dumps(collect(), default=str).encode(), "application/json")
        elif self.path == "/api/compare/history":        # 竞技场历史 + 记分板
            runs = compare_history.load_runs(load_settings().home)
            self._send(json.dumps(_compare_history_response(runs)).encode(), "application/json")
        elif self.path.startswith("/api/models"):        # 模型目录（可选 provider 查询参数）
            from urllib.parse import parse_qs, urlparse

            prov = parse_qs(urlparse(self.path).query).get("provider", [None])[0]
            self._send(json.dumps(list_models(prov)).encode(), "application/json")
        elif self.path.startswith("/api/events"):        # 增量追踪事件（cursor 从上次续读）
            from urllib.parse import parse_qs, urlparse

            raw = parse_qs(urlparse(self.path).query).get("cursor", [None])[0]
            cursor = int(raw) if raw and raw.lstrip("-").isdigit() else None
            self._send(json.dumps(events_since(cursor)).encode(), "application/json")
        elif self.path.startswith("/api/reveal"):        # 在编辑器 / Finder 里打开路径
            from urllib.parse import parse_qs, unquote, urlparse

            rel = unquote(parse_qs(urlparse(self.path).query).get("path", [""])[0])
            self._send(json.dumps(reveal_path(rel)).encode(), "application/json")
        elif self.path.startswith("/static/"):           # 前端静态文件
            self._serve_static(self.path)
        else:                                            # 其它任何路径都给 SPA 入口
            self._send((STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")

    def _serve_static(self, path: str) -> None:  # 前端文件
        name = path.split("/static/", 1)[1].split("?")[0]
        target = (STATIC / name).resolve()
        if STATIC.resolve() not in target.parents or not target.is_file():
            self.send_response(404)
            self.end_headers()
            return
        ctype = {".css": "text/css", ".js": "text/javascript",
                 ".html": "text/html; charset=utf-8"}.get(target.suffix, "application/octet-stream")
        self._send(target.read_bytes(), ctype, no_cache=True)

    def do_POST(self):  # noqa: N802 — 本地写端点
        length = int(self.headers.get("Content-Length", 0))
        # /api/voice 接收的是原始音频块，不是 JSON——先处理它。
        if self.path == "/api/voice":
            raw = self.rfile.read(length)
            self._send(json.dumps(transcribe_audio(raw)).encode(), "application/json")
            return
        # /api/chat/stream 在对话轮次运行时流式输出框架/运行时事件（SSE）。
        if self.path == "/api/chat/stream":
            payload = json.loads(self.rfile.read(length) or "{}")
            message = (payload.get("message") or "").strip()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def emit(kind, ev):
                try:
                    self.wfile.write(f"data: {json.dumps({'kind': kind, **ev}, default=str)}\n\n".encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # 浏览器在流式中途跳走了——没关系

            if not message:
                emit("done", {"error": "empty message"})
                return
            try:
                chat_stream(message, emit)
            except Exception as exc:  # 作为终止事件浮出，不要返回 500
                emit("done", {"error": f"{type(exc).__name__}: {exc}"})
            return
        # /api/compare/stream 让多个模型开跑，每个结果落地时发出来。
        if self.path == "/api/compare/stream":
            payload = json.loads(self.rfile.read(length) or "{}")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def emit(kind, ev):
                try:
                    self.wfile.write(f"data: {json.dumps({'kind': kind, **ev}, default=str)}\n\n".encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            try:
                compare_stream((payload.get("message") or "").strip(), payload.get("models") or [],
                               emit, judge=bool(payload.get("judge")), coding=bool(payload.get("coding")),
                               judge_spec=(payload.get("judge_model") or ""), apple=bool(payload.get("apple")))
            except Exception as exc:
                emit("done", {"error": f"{type(exc).__name__}: {exc}"})
            return
        routes = {"/api/chat": None, "/api/memory": memory_action, "/api/settings": apply_settings,
                  "/api/query": run_query, "/api/session": session_action, "/api/pin": pin_action,
                  "/api/compare": compare_models, "/api/compare/clear": compare_clear,
                  "/api/compare/regrade": compare_regrade, "/api/compare/delete_run": compare_delete_run}
        if self.path not in routes:
            self.send_response(404)
            self.end_headers()
            return
        payload = json.loads(self.rfile.read(length) or "{}")
        try:
            if self.path == "/api/chat":
                message = (payload.get("message") or "").strip()
                out = chat(message) if message else {"error": "empty message"}
            else:
                out = routes[self.path](payload)
        except Exception as exc:  # 浮出，不要 500——浏览器会显示它
            out = {"error": f"{type(exc).__name__}: {exc}"}
        self._send(json.dumps(out, default=str).encode(), "application/json")

    def log_message(self, *args):  # 让终端保持安静
        pass


def main() -> None:
    # 端口优先级：WAKU_DASHBOARD_PORT，然后是约定的 PORT（部署平台和 IDE
    # 预览窗格会用到），最后是 7777。如果被占用，就往后走。
    base = int(os.getenv("WAKU_DASHBOARD_PORT") or os.getenv("PORT") or PORT)
    for port in range(base, base + 10):  # 绕过被占用的端口，而不是崩溃
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        except OSError:
            print(f"port {port} busy, trying {port + 1}…")
            continue
        # 一条命令，多个网关：如果设置了 Telegram token，也把机器人跑起来
        # （后台线程），这样你不需要单独的 `waku telegram`。
        try:
            from waku.gateway.telegram import start_in_background

            if start_in_background():
                print("Telegram gateway → listening in the background (phone messages land here too)")
        except Exception as exc:  # noqa: BLE001 — 绝不让一个网关阻塞 dashboard
            print(f"(telegram) not started: {exc}")
        print(f"Waku dashboard → http://localhost:{port}  (Ctrl-C to stop)")
        server.serve_forever()
        return
    raise SystemExit(f"no free port in {base}–{base + 9}")


if __name__ == "__main__":
    main()
