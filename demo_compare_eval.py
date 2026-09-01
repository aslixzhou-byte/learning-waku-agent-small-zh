"""demo_compare_eval.py —— 模型对比 demo，直接调用 dashboard-study.py 的 compare 函数。

演示：一条任务，让多个模型各跑一遍（隔离沙箱 + 完整 agent 循环），
并排对比 速度 / 成本 / 完成度 / 质量 的差异。

跑法：
    python demo_compare_eval.py
    python demo_compare_eval.py "帮我约 Alex 明天开会" deepseek:deepseek-v4-pro kimi:kimi-k3
    python demo_compare_eval.py --no-judge          # 跳过质量打分（省一次裁判调用）

会真实调用各模型的 API（花钱，deepseek/kimi 都很便宜）。
"""

from __future__ import annotations

import importlib  # dashboard-study.py 文件名带连字符，不能用普通 import，用 importlib 加载
import sys

# 加载 waku/ops/dashboard-study.py（带连字符的模块名）
ds = importlib.import_module("waku.ops.dashboard-study")
compare_stream = ds.compare_stream

DEFAULT_MESSAGE = "Schedule a coffee with Alex next Tuesday at 9am"  # 能匹配 dataset.jsonl → 有完成度分
DEFAULT_SPECS = ["deepseek:deepseek-v4-pro", "deepseek:deepseek-v4-flash"]


def main() -> None:
    args = sys.argv[1:]
    message = args[0] if args and not args[0].startswith("--") else DEFAULT_MESSAGE
    specs = [a for a in args if ":" in a] or DEFAULT_SPECS
    judge = "--no-judge" not in args

    print("=" * 78)
    print("模型对比（Compare）demo")
    print(f"任务: {message}")
    print(f"参赛模型: {', '.join(specs)}")
    print(f"质量打分: {'开' if judge else '关'}")
    print("=" * 78 + "\n")

    collected: list = []  # 收集每个模型的结果，最后画对比表

    def emit(kind: str, ev: dict) -> None:
        """把 SSE 事件打印到终端（代替浏览器），并收集 result 画对比表。"""
        spec = ev.get("spec", "")
        if kind == "start":
            print(f"[{spec}] 开始跑...")
        elif kind == "gate":
            print(f"[{spec}]   门禁: {ev.get('decision')} — {ev.get('reason', '')}")
        elif kind == "tool":
            print(f"[{spec}]   工具: {ev.get('tool')}")
        elif kind == "result":
            if ev.get("error"):
                print(f"[{spec}]   [失败] {ev['error']}")
            else:
                c = ev.get("completion")
                done = ("通过" if c and c.get("passed") else "失败") if c else "—（非已知用例）"
                print(f"[{spec}]   [完成] {ev['latency_ms']}ms · ${ev['cost_usd']} · 完成度[{done}]")
                collected.append(ev)
        elif kind == "grading":
            print(f"\n>>> 开始质量打分（{ev['n']} 个模型，裁判 {ev.get('judge')}）...")
        elif kind == "grade":
            q = ev.get("quality") or {}
            print(f"[{spec}]   质量: {q.get('score', '?')}/10 — {q.get('reason', '')}")
        elif kind == "done":
            print("\n>>> 全部完成，已持久化到 <home>/compare/history.jsonl")

    compare_stream(message, specs, emit, judge=judge)

    # 画对比表
    if collected:
        print("\n" + "=" * 78)
        print("对比表（差异一览）")
        print("=" * 78)
        print(f"{'模型':<32}{'速度':>10}{'成本':>10}{'完成度':>10}{'质量':>8}")
        print("-" * 78)
        for ev in collected:
            spec = ev["spec"]
            lat = f"{ev['latency_ms']}ms"
            cost = f"${ev['cost_usd']}"
            c = ev.get("completion")
            done = ("通过" if c.get("passed") else "失败") if c else "—"
            q = ev.get("quality") or {}
            score = f"{q.get('score', '—')}/10"
            print(f"{spec:<32}{lat:>10}{cost:>10}{done:>10}{score:>8}")
        print("=" * 78)


if __name__ == "__main__":
    main()
