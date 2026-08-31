"""评测电池的确定性「完成度」评分——唯一的评分器。

`scripts/shootout.py`（CLI 表格）和对比竞技场（实时 dashboard 记分板）
用同一种方式给模型的运行打分：预期的工具有没有触发、参数对不对、循环是否
真正跑够了。把这个判定放在这里，终端数字和屏幕数字就永远不会分叉。

一个「用例」是 `evals/dataset.jsonl` 的一行：一条输入提示词加上它预期的
结果（`expect_tool` / `expect_in_args` / `expect_min_tool_calls` /
`setup_fact`）。完成度是诚实、无需裁判的轴——它是 tau-bench / SWE-bench
风格结果检查（终态是否匹配）的本地镜像，而不是凭感觉。见 docs/benchmarks.md。
"""

from __future__ import annotations

import json                # 解析数据集每一行
from pathlib import Path

# 电池用例文件的位置：仓库根的 evals/dataset.jsonl。
_DATASET = Path(__file__).resolve().parents[2] / "evals" / "dataset.jsonl"


def load_cases() -> list[dict]:
    """按文件顺序返回全部电池用例；数据集缺失时返回空列表。"""
    if not _DATASET.exists():     # 仓库里没带数据集时
        return []
    return [json.loads(line) for line in _DATASET.read_text().splitlines() if line.strip()]
    # 每非空行就是一个用例 dict；跳过空行避免崩溃


def check_case(case: dict, tool_calls: list[dict]) -> tuple[bool, str]:
    """确定性契约：正确的工具、正确的参数、足够的调用次数。返回 (passed, why)——
    成功时 why 为 'ok'，否则为第一个失败预期（足够短，能显示在对比列下或念出来）。"""
    fired = [c["tool"] for c in tool_calls]      # 提取本轮实际触发的工具名列表
    if case.get("expect_tool") is None:          # 该用例根本不预期任何工具被触发
        return (not fired, "no tool expected" if not fired else f"called {fired}")
    if case["expect_tool"] not in fired:         # 预期工具没出现 → 直接失败
        return (False, f"expected {case['expect_tool']}, called {fired or 'nothing'}")
    args = next(c["args"] for c in tool_calls if c["tool"] == case["expect_tool"])  # 找到该工具的参数
    for key, needle in case.get("expect_in_args", {}).items():  # 逐项检查参数里的预期子串
        if needle.lower() not in str(args.get(key, "")).lower():  # 大小写不敏感匹配
            return (False, f"'{needle}' not in args[{key}]")
    want = case.get("expect_min_tool_calls", 0)  # 至少要求的工具调用次数
    if len(fired) < want:                        # 调用次数不足 → 失败
        return (False, f"only {len(fired)} tool calls, wanted >= {want}")
    return (True, "ok")                          # 全部满足 → 通过


def case_for_message(message: str, cases: list[dict] | None = None) -> dict | None:
    """输入与该竞技场提示词匹配（去除首尾空白后的精确匹配）的电池用例，
    这样跑在「已知任务」上的对比就能拿到完成度分数。对自由形式的提示词返回
    None——竞技场仍会展示速度/成本/token，只是没有分数。"""
    msg = (message or "").strip()        # 归一化输入
    for case in (cases if cases is not None else load_cases()):   # 没传列表就用默认数据集
        if (case.get("input") or "").strip() == msg:   # 与用例的 input 精确匹配
            return case
    return None                          # 没有匹配 → 自由形式提示词
