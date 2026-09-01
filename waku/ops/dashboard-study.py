"""dashboard-study.py —— 从 dashboard.py 精简出的 Compare 相关逻辑。

只保留「模型对比」这一块：_compare_one / compare_models / compare_stream
（多模型并排跑同一任务）+ 历史管理（clear / regrade / delete）+ 定价（price_for）。
供研究 Compare 实现或迁移到 FastAPI 时参考。
"""

from __future__ import annotations

import json        # 解析用量 JSONL 行
from pathlib import Path   # 路径对象（临时 home）

from waku.config import load_settings     # 读取 home 目录（Compare 历史所在）
from waku.ops import compare_history, judge as judge_mod, scoring   # Compare 历史 / 裁判 / 完成度

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

    # 把这场对比持久化到Compare自己的历史（绝不写智能体的真实状态）。
    try:
        compare_history.append_run(load_settings().home, message, collected)
    except Exception:
        pass   # 历史写入的偶然闪失绝不能让整场对比失败
    emit("done", {})


def compare_clear(payload: dict) -> dict:
    """清空对比记分板/历史（Clear 按钮）。只动Compare自己的日志；其它一概不碰。"""
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
