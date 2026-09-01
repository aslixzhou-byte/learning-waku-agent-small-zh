"""
发布门禁
python -m waku.ops.release_gate
确定性评测必须 100% 通过，它们就是单元测试；任一失败即阻止发布。
当存在密钥时运行裁判评测，并汇报分数。退出码 0 = 可以发布。

1. evals/deterministic
2. evals/judge
"""

from __future__ import annotations

import os
import re
import subprocess # 启动 pytest 子进程
import sys # 复用当前解释器来跑 pytest
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO = Path(__file__).resolve().parents[2]   # 仓库根目录（evals 所在处）


def run(suite: str) -> tuple[int, dict]:
    """运行一个 pytest 套件；实时流式输出（这样门禁看起来不会像卡住），
    然后从 -q 摘要行解析出 {passed, failed}。"""
    print(f"\n=== {suite} ===", flush=True)
    print(f"(running pytest evals/{suite} — progress below)", flush=True)
    # 子进程不加缓冲，这样 Windows/Git-Bash 能在测试逐个完成时显示圆点，
    # 而不是等整个套件跑完才一下子显示。
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(                # 异步启动 pytest，边跑边读
        [sys.executable, "-u", "-m", "pytest", "-q", str(REPO / "evals" / suite)],
        cwd=REPO,                           # 在仓库根目录运行（保证相对导入正确）
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,           # 合并 stderr，避免两条流交错
        text=True,
        encoding="utf-8",
        errors="replace",                   # 遇到坏字节也不崩
        env=env,
        bufsize=1,
    )
    # 按块读取而不是按行：pytest -q 在摘要出现前输出的 "." 没有换行，
    # 所以按行缓冲在整个套件期间仍会显得像卡住了一样。
    chunks: list[str] = []
    assert proc.stdout is not None
    while True:
        chunk = proc.stdout.read(64)        # 每块 64 字节，边读边打印
        if not chunk:                       # EOF = pytest 结束
            break
        chunks.append(chunk)
        print(chunk, end="", flush=True)    # 原样回显给用户
    code = proc.wait()                      # 取退出码（0 = 全部通过）
    out = "".join(chunks)
    counts = {                              # 从 "N passed" / "N failed" 摘要里抓数字
        k: (int(m.group(1)) if (m := re.search(rf"(\d+) {k}", out)) else 0)
        for k in ("passed", "failed")
    }
    return code, counts


def report(deterministic: str, judge: str, suites: dict | None = None) -> None:
    """持久化最新裁决，并把它追加到运行历史里。"""
    from datetime import datetime, timezone
    import json

    from waku.config import load_settings

    settings = load_settings()
    settings.ensure_home()                  # 确保 .waku 目录存在
    record = {
        "deterministic": deterministic,     # 确定性评测结果（pass/fail）
        "judge": judge,                     # 裁判评测结果（pass/fail/skipped/not run）
        "suites": suites or {},             # 各套件的原始统计
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (settings.home / "eval_report.json").write_text(json.dumps(record))  # 覆盖最新裁决
    with (settings.home / "eval_runs.jsonl").open("a") as f:   # 追加到运行历史
        f.write(json.dumps(record) + "\n")


def main() -> None:
    suites = {}
    code, suites["deterministic"] = run("deterministic")   # 第一步：确定性评测套件
    if code:                            # 任一失败即阻止发布
        report("fail", "not run", suites)
        print("\nGATE CLOSED — deterministic evals failed. Fix before releasing.")
        sys.exit(1)

    # 裁判需要「当前模型提供方」的密钥（anthropic、openrouter 等），
    # 与 evals/helpers.HAS_KEY 的规则一致
    from waku.config import load_settings
    from waku.loop.models import PROVIDERS

    settings = load_settings()
    provider = PROVIDERS.get(settings.provider)
    if settings.api_key or (provider and os.getenv(provider.key_env)):   # 有密钥才跑裁判
        code, suites["judge"] = run("judge")
        if code:
            report("pass", "fail", suites)
            print("\nGATE CLOSED — judge scores below threshold.")
            sys.exit(1)
        report("pass", "pass", suites)  # 裁判也过了 → 记录双 pass
    else:
        report("pass", "skipped", suites)   # 没密钥：记录为 skipped，不算失败
        print(f"\n(judge suite skipped: no API key for provider '{settings.provider}')")

    print("\nGATE OPEN — safe to release.")


if __name__ == "__main__":
    main()
