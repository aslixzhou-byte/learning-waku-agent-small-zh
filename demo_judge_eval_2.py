"""judge 测评 demo 2 用 ops/judge.py 的裁判。

和 demo_judge_eval.py（evals 那套）的区别：
  1. 评分标准是固定的 _RUBRIC（通用质量分级），不是每条可定制的 criteria
  2. 默认裁判是 ，不是当前 provider（deepseek）
  3. 多了 tools 参数：把「本轮实际触发的工具」作为事实依据传给裁判，
     防止它把「我保存了」这种真实声明误判为幻觉

跑法：
    python demo_judge_eval_2.py # 默认用 gpt-5.6-sol 当裁判（需 OPENAI_API_KEY）
    python demo_judge_eval_2.py --judge deepseek  # 改用 deepseek 当裁判（省钱）
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from waku.app import Waku
from waku.config import Settings
from waku.ops.judge import JUDGE_MODEL, JUDGE_PROVIDER, judge_reply

THRESHOLD = 6


def main() -> None:
    # 可选：--judge deepseek 用 deepseek 当裁判（否则用默认 gpt-5.6-sol）
    judge_provider = None
    judge_model = None
    if "--judge" in sys.argv:
        val = sys.argv[sys.argv.index("--judge") + 1]
        if ":" in val:
            judge_provider, judge_model = val.split(":", 1)
        else:
            judge_provider = val
        print(f"(改用裁判: {judge_provider}:{judge_model or '默认'})\n")

    # ---- 第 1 步：跑一轮 Waku，拿到回复 + 实际触发的工具
    task = "Schedule a coffee with Alex next Tuesday at 9am"
    print("=" * 72)
    print(f"用户输入: {task}\n")

    home = Path(tempfile.mkdtemp(prefix="demo-judge2-"))
    app = Waku(settings=Settings(home=home, apple_calendar=False))
    app.session.session_id = "demo"

    print(">>> Waku 跑起来了...\n")
    result = app.respond(task, source="demo")
    reply = result.reply
    tools = [c["tool"] for c in result.tool_calls]   # 本轮实际触发的工具名列表

    print(f"Waku 回复: {reply}\n")
    print(f"实际触发的工具: {tools or '（无）'}\n")

    # ---- 第 2 步：用 ops/judge.py 的裁判打分（固定 _RUBRIC + tools 事实依据）
    print(f"裁判: {judge_provider or JUDGE_PROVIDER}:{judge_model or JUDGE_MODEL}")
    print("评分标准: _RUBRIC（固定，通用质量分级，不是自定义 criteria）")
    print("          - 9-10 完全命中 / 5-8 大体命中 / 1-4 部分命中 / 0 忽略")
    print("          - 关键：tools 列表里的工具「真的跑过」，声明与之相符不扣分\n")
    print(">>> 裁判打分中...\n")

    verdict = judge_reply(task, reply, provider=judge_provider, model=judge_model, tools=tools)

    # ---- 第 3 步：打印结果
    if verdict is None:
        print("  裁判无法评分（回复为空 / 裁判不可达 / JSON 解析失败 → 返回 None）")
        return

    score = verdict["score"]
    reason = verdict["reason"]
    judge = verdict["judge"]
    passed = score >= THRESHOLD

    print("-" * 72)
    print(f"  得分: {score}/10  （阈值 {THRESHOLD}）")
    print(f"  理由: {reason}")
    print(f"  裁判: {judge}")
    print(f"  [{'通过' if passed else '失败'}]")
    print("=" * 72 + "\n")

    # 对照：tools 参数的作用: 如果不传 tools，裁判不知道工具真的跑过
    print("补充说明：")
    print("  tools 参数传的是「本轮实际触发的工具名」，作为 ground truth 给裁判。")
    print(f"  本例传了 tools={tools}，所以裁判知道 create_event 真的跑了，")
    print("  「已帮你约好」这种声明不会被误判成幻觉扣分。")


if __name__ == "__main__":
    main()
