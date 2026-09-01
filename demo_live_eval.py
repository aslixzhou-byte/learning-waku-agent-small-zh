"""live 测评 demo 用真实模型跑 evals/dataset.jsonl，演示「看什么、怎么看」。
跑法：
    python demo_live_eval.py # 只跑第 1 个 case（schedule-basic，最快）
    python demo_live_eval.py --all  # 跑全部 11 个 case

会真实调用 .env 里配的 provider（deepseek）的模型，所以会花钱（deepseek 很便宜）。

演示的完整流程：
  1. 从 dataset.jsonl 读一个「用例」，告诉你它的【预期】是什么
  2. 用真实模型跑这个【输入】，看它【实际】调了什么工具、参数是什么
  3. 用 scoring.check_case 做【判定】：预期 vs 实际，过还是不过、为什么
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from waku.app import Waku
from waku.config import Settings
from waku.ops import scoring

"""
{
    "id": "schedule-basic", 
    "input": "Schedule a coffee with Alex next Tuesday at 9am",
    "expect_tool": "create_event",
    "expect_in_args": {
        "title": "alex",
        "start": "T09:00"
        }
}
"""

def run_one(case: dict) -> bool:
    """跑一个 case（独立 home 隔离，避免前置事实互相污染），打印预期/实际/判定。"""
    print("=" * 72)
    print(f"用例 [{case['id']}]") # schedule-basic

    # ---- 第 1 步：这个用例的「预期」是什么
    print(f"  输入（给模型的话）: {case['input']}") # Schedule a coffee with Alex next Tuesday at 9am
    if case.get("expect_tool") is None:
        print("  预期: 不调用任何工具")
    else:
        print(f"  预期工具: {case['expect_tool']}") # create_event
        if case.get("expect_in_args"):
            print(f"  预期参数里要含: {case['expect_in_args']}") # "title": "alex" "start": "T09:00"
        if case.get("expect_min_tool_calls"):
            print(f"  最少工具调用次数: {case['expect_min_tool_calls']}")

    # ---- ---------------------------------------------------

    # 每个 case 独立 home，避免多个 case 的记忆互相污染
    home = Path(tempfile.mkdtemp(prefix="demo-eval-"))
    app = Waku(settings=Settings(home=home, apple_calendar=False))
    app.session.session_id = "demo"

    # 有些用例要求「先记住一个偏好」，再让模型应用它
    if case.get("setup_fact"):
        app.memory.facts.add(case["setup_fact"]["subject"], case["setup_fact"]["content"])
        print(f"  （预置记忆: {case['setup_fact']['subject']} → {case['setup_fact']['content']}）")

    # ---- 第 2 步：用真实模型跑这个输入，看它实际做了什么
    print("\n  >>> 真实模型跑起来了...\n")
    result = app.respond(case["input"], source="demo")

    print("  模型实际调用:")
    if not result.tool_calls:
        print("    （没有调用任何工具）")
    for c in result.tool_calls:
        print(f"    - {c['tool']}  参数={c['args']}")
    print(f"  模型回复: {result.reply[:150]}")
    print(f"  迭代 {result.iterations} 轮")

    # ---- 第 3 步：判定（预期 vs 实际）
    passed, why = scoring.check_case(case, result.tool_calls)
    print("-" * 72)
    print(f"  [{'通过' if passed else '失败'}] {why}")
    print("=" * 72 + "\n")
    return passed


def main() -> None:
    cases = scoring.load_cases()
    if not cases:
        print("没找到 evals/dataset.jsonl")
        return

    run_all = "--all" in sys.argv
    selected = cases if run_all else cases[:1]
    if not run_all:
        print(f"(默认只跑第 1 个 case；加 --all 跑全部 {len(cases)} 个)\n")
    results = [(c["id"], run_one(c)) for c in selected]

    print(f"汇总: {sum(1 for _, p in results if p)}/{len(results)} 通过")
    for cid, p in results:
        print(f"  [{'PASS' if p else 'FAIL'}] {cid}")


if __name__ == "__main__":
    main()
