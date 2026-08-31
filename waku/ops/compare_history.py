"""对比竞技场历史——基准测试自己专属的追加日志。

刻意与智能体的真实状态（state.db / MEMORY.md / traces / usage.jsonl）分离。
一次对比运行就是一次基准测试：一个提示词在一次性沙箱里跑过 N 个模型。
它既不是一段对话、一条记忆，也不是单模型的追踪，所以绝不能落入
chat_log / facts / calendar（那会污染循环/记忆/数据库/运维各视图，并破坏
竞技场赖以工作的沙箱隔离）。

因此它住进自己的 JSONL——每场对比一行——与仓库里其它追加日志
（usage.jsonl、traces/*.jsonl、eval_runs.jsonl）保持一致。以读为主；
按模型的记分板只是一次简单扫描，所以不值得建 SQLite 表。

文件：``<home>/compare/history.jsonl``——最新的在最后，并限制为最近的
``MAX_RUNS`` 场，保持小巧。本模块是那个文件的唯一所有者；dashboard 只调用
append_run / load_runs / aggregate。
"""

from __future__ import annotations

import json                               # 序列化 / 解析 JSONL 的每一行
from datetime import datetime, timezone   # 生成 UTC 时间戳（精确到秒）
from pathlib import Path                  # home 目录路径

MAX_RUNS = 50      # 保持日志小巧；更早的对比会从前面滚出
REPLY_CAP = 1000   # 截断存储的回复，避免文件膨胀


def _path(home: Path) -> Path:
    return home / "compare" / "history.jsonl"   # 历史文件固定位于 <home>/compare/ 下


def _slim(r: dict) -> dict:
    """只保留历史列表 + 记分板需要的字段，并给回复加上限长。
    接受竞技场产出的每个模型的结果 dict（gate 是 {decision, reason} 对象、
    tools 是 [{tool}]）并把它展平。"""
    gate = r.get("gate") or {}            # gate 可能是 dict 或 None；统一成 dict
    return {
        "spec": r.get("spec") or f"{r.get('provider')}:{r.get('model')}",  # 「提供方:模型」标识
        "provider": r.get("provider"),
        "model": r.get("model"),
        "latency_ms": r.get("latency_ms"),      # 单轮延迟（毫秒）
        "tokens_in": r.get("tokens_in"),
        "tokens_out": r.get("tokens_out"),
        "cost_usd": r.get("cost_usd"),          # 估算成本
        "iterations": r.get("iterations"),      # 循环轮数
        "gate": gate.get("decision") if isinstance(gate, dict) else gate,  # 门禁决策（跳过/放行等）
        "tools": [t.get("tool") for t in (r.get("tools") or [])],          # 只保留工具名列表
        "error": r.get("error"),                # 出错运行的错误信息
        "completion": r.get("completion"),   # 已评分用例上是 {passed, why, case}，否则为 None
        "quality": r.get("quality"),         # 经 K3 裁判评过分为 {score, reason, judge}，否则为 None
        "reply": (r.get("reply") or "")[:REPLY_CAP],   # 回复限长，防止文件膨胀
    }


def append_run(home: Path, message: str, results: list[dict], ts: str | None = None) -> None:
    """追加一场已完成的对比，并裁剪到最近的 MAX_RUNS 场。

    `results` 是竞技场已经构建好的每个模型的结果 dict 列表。
    重写整个（受限长的）文件——因为构造上它很小，所以没问题。"""
    record = {
        "ts": ts or datetime.now(timezone.utc).isoformat(timespec="seconds"),  # 默认用当前 UTC 时间
        "message": message,                       # 触发这场对比的提示词
        "results": [_slim(r) for r in results],   # 每模型结果先瘦身再持久化
    }
    runs = load_runs(home)                        # 读取现有历史
    runs.append(record)                           # 新的一场追加到末尾
    save_runs(home, runs)                         # 整体回写（文件很小，可接受）


def save_runs(home: Path, runs: list[dict]) -> None:
    """重写（受限长的）历史文件——被 append_run 和重评分使用，
    后者会就地修改某场已存在对比的已存结果。"""
    path = _path(home)
    path.parent.mkdir(parents=True, exist_ok=True)      # 确保 compare/ 目录存在
    path.write_text("\n".join(json.dumps(r) for r in runs[-MAX_RUNS:]) + "\n")
    # 只保留最近 MAX_RUNS 场；每场一行 JSON，末尾补一个换行


def clear(home: Path) -> None:
    """清空对比历史（记分板上的 Clear 按钮）。只移除竞技场自己的日志——其它一概不动。"""
    _path(home).unlink(missing_ok=True)   # 删除文件；不存在也不报错


def load_runs(home: Path, limit: int | None = None) -> list[dict]:
    """最近的若干场对比，按旧 -> 新排列。`limit` 只返回最后 N 场。"""
    path = _path(home)
    if not path.exists():                 # 还没有任何对比时
        return []
    runs = []
    for line in path.read_text().splitlines():   # 逐行读取
        line = line.strip()
        if not line:                      # 跳过空行
            continue
        try:
            runs.append(json.loads(line)) # 每行一个 JSON 对象
        except json.JSONDecodeError:
            pass                          # 损坏的行直接跳过，不让读取整体崩溃
    return runs[-limit:] if limit else runs   # 取最后 N 场，或全部


def aggregate(runs: list[dict]) -> list[dict]:
    """给定若干场对比的按模型记分板：运行次数、成功次数，以及成功运行上的
    延迟 / token / 成本的累计合计（出错运行的次数计入 `runs`，但不给合计加任何值）。
    总成本最省的排最前；前端可以按任意列重新排序。"""
    acc: dict[str, dict] = {}             # spec -> 累计行
    for run in runs:                      # 遍历每一场对比
        for r in run.get("results", []):  # 再遍历该场的每个模型结果
            spec = r.get("spec") or f"{r.get('provider')}:{r.get('model')}"  # 汇总键
            a = acc.setdefault(spec, {"spec": spec, "provider": r.get("provider"),
                                      "model": r.get("model"), "runs": 0, "ok": 0,
                                      "lat": 0, "tin": 0, "tout": 0, "cost": 0.0,
                                      "passed": 0, "scored": 0, "qsum": 0, "qn": 0})
            a["runs"] += 1                # 运行次数 +1
            if not r.get("error"):        # 只有成功运行才累计资源消耗
                a["ok"] += 1
                a["lat"] += r.get("latency_ms") or 0      # 延迟合计
                a["tin"] += r.get("tokens_in") or 0       # 输入 token 合计
                a["tout"] += r.get("tokens_out") or 0     # 输出 token 合计
                a["cost"] += r.get("cost_usd") or 0.0     # 成本合计
            # 完成度：只有跑在「已知电池用例」上的对比才有裁决。
            c = r.get("completion")
            if c is not None:
                a["scored"] += 1
                a["passed"] += 1 if c.get("passed") else 0   # 通过数
            q = r.get("quality")
            if q is not None and q.get("score") is not None:  # 有裁判分数才累计
                a["qsum"] += q["score"]   # 分数累加（供求平均）
                a["qn"] += 1              # 评分样本数
    out = [{"spec": a["spec"], "provider": a["provider"], "model": a["model"],
            "runs": a["runs"], "ok": a["ok"], "total_latency_ms": a["lat"],
            "total_tokens_in": a["tin"], "total_tokens_out": a["tout"],
            "total_tokens": a["tin"] + a["tout"],  # 为向后兼容 / 排序保留
            "cases_passed": a["passed"], "cases_scored": a["scored"],
            "quality_n": a["qn"],
            "quality_avg": round(a["qsum"] / a["qn"], 1) if a["qn"] else None,  # 平均分；无样本则 None
            "total_cost_usd": round(a["cost"], 4)} for a in acc.values()]
    out.sort(key=lambda x: x["total_cost_usd"])   # 默认按总成本升序（最便宜在前）
    return out
