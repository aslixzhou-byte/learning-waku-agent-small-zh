"""一个带日期、自文档化的委托编码工作区。

pi（通过 delegate_task）会写入真实的文件。如果没有归属地，它们会
在临时目录里消失——所以每次临时委托都会落到

    <WAKU_WORKSPACE>/<YYYY-MM-DD>/<HHMMSS>-<model>-<slug>/
        <pi 写出的文件>
        MANIFEST.md      日期、模型、任务、创建的文件、自动运行结果
        run.log          自动运行的 stdout/退出码
        pi-transcript.log

这样一次编码运行是可追踪的，而不是神秘的临时目录。代码产物是
交付物，而不是代理状态（记忆 / 日历 / 数据库），所以它刻意放在
.waku 之外并加入 gitignore——它绝不会污染代理的真实状态或仓库。

自动运行：pi 结束后，入口脚本会被运行（无头、捕获输出、带超时），
结果会写入清单并回传给循环——这样模型能亲眼看到自己的代码是否
真的运行了，并能作出反应。
"""

from __future__ import annotations  # 让类型注解（tuple | None）在旧版 Python 里也能用

import os         # 读取 WAKU_WORKSPACE 等环境变量
import re         # 从任务描述提取 slug 词
import subprocess # 自动运行入口脚本
import sys        # 取 python 解释器路径
import time       # 测量自动运行耗时
from datetime import datetime  # 生成带日期/时间的文件夹名
from pathlib import Path       # 路径类型

WORKSPACE_ENV = "WAKU_WORKSPACE"          # 根目录；默认 ./waku_workspace
AUTORUN_ENV = "WAKU_DELEGATE_AUTORUN"     # 设为 "0"/"false"/"no" 可禁用自动运行
RUN_TIMEOUT = int(os.getenv("WAKU_AUTORUN_TIMEOUT", "30"))  # 自动运行的超时秒数（默认 30）
_OURS = {"MANIFEST.md", "run.log", "pi-transcript.log"}  # 我们自己的文件，不算作 pi 产物
_ENTRY_PREFS = ("main.py", "app.py", "run.py", "game.py")  # 入口脚本候选名，按优先级


def workspace_root() -> Path:
    return Path(os.getenv(WORKSPACE_ENV, "waku_workspace")).expanduser().resolve()  # 根目录：环境变量优先否则默认；展开 ~ 并取绝对路径


def _slug(text: str, n: int = 4) -> str:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())  # 只留小写字母/数字词
    return "-".join(words[:n]) or "task"  # 取前 n 个词拼连字符；没词就回退 "task"


def new_run_folder(model: str, task: str, now: datetime | None = None) -> Path:
    """创建并返回一次委托的全新带日期运行文件夹。"""
    now = now or datetime.now()  # 允许测试注入时间
    name = f"{now.strftime('%H%M%S')}-{_slug(model, 2)}-{_slug(task)}"  # HHMMSS-模型-任务 命名
    folder = workspace_root() / now.strftime("%Y-%m-%d") / name  # 按日期分目录
    folder.mkdir(parents=True, exist_ok=True)  # 一次性建全目录
    return folder


def created_files(folder: Path) -> list[Path]:
    """pi 在 `folder` 里写的所有东西——排除我们自己的清单/日志和
    __pycache__——最新的在前。"""
    files = [p for p in folder.rglob("*")  # 递归找全部文件
             if p.is_file() and p.name not in _OURS and "__pycache__" not in p.parts]  # 剔除我们的文件与缓存
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)  # 按修改时间倒序（最新在前）


def _pick_entry(files: list[Path]) -> Path | None:
    py = [p for p in files if p.suffix == ".py"]  # 只考虑 Python 文件
    if not py:
        return None
    by_name = {p.name: p for p in py}  # 名字 → 路径，便于按名查找
    for pref in _ENTRY_PREFS:  # 按优先级找 main.py / app.py / ...
        if pref in by_name:
            return by_name[pref]
    with_main = [p for p in py if "__main__" in p.read_text(errors="ignore")]  # 找带 __main__ 块的
    if with_main:
        return with_main[0]
    return py[0] if len(py) == 1 else None  # 恰好一个 .py 才算可运行；多个则无法确定入口


def autorun(folder: Path) -> tuple | None:
    """运行 `folder` 里的入口 .py（无头、捕获输出、带超时）。返回
    (entry_name, exit_code, output, seconds) —— exit_code 为 None 表示它仍在
    运行（可能是交互式的）并被停止了。没有可运行内容或自动运行被
    禁用时返回 None。"""
    if os.getenv(AUTORUN_ENV, "1") in ("0", "false", "no"):  # 显式禁用则跳过
        return None
    entry = _pick_entry(created_files(folder))  # 先找入口脚本
    if entry is None:  # 没有可运行的 .py
        return None
    t0 = time.perf_counter()  # 计时开始
    try:
        r = subprocess.run([sys.executable, entry.name], cwd=folder,  # 用当前解释器运行，工作目录设为工作区
                           stdin=subprocess.DEVNULL, capture_output=True, text=True,  # 无头：无 stdin，捕获输出
                           timeout=RUN_TIMEOUT, check=False)
        out = (r.stdout + r.stderr).strip()  # 合并 stdout/stderr
        result = (entry.name, r.returncode, out, round(time.perf_counter() - t0, 1))  # 退出码 0 = 跑通了
    except subprocess.TimeoutExpired:  # 超时：视为可能交互/卡住，报告"仍在运行"
        result = (entry.name, None,
                  f"(still running after {RUN_TIMEOUT}s — likely interactive; stopped)",
                  RUN_TIMEOUT)
    except OSError as exc:  # 启动失败
        result = (entry.name, -1, f"couldn't launch: {exc}", 0.0)
    (folder / "run.log").write_text(  # 每次自动运行都留一份 run.log
        f"$ python3 {result[0]}\nexit: {result[1]}\n\n{result[2]}\n")
    return result  # 元组回传给循环，让模型看到运行结果


def write_manifest(folder: Path, provider: str, model: str, task: str,
                   files: list[Path], run: tuple | None) -> None:
    """记录这次运行：日期（来自文件夹名）、模型、任务、文件、运行结果。"""
    when = f"{folder.parent.name} {folder.name.split('-')[0]}"  # 从路径里反推出 日期 + HHMMSS
    lines = [f"# Delegated coding run — {when}", "",  # MANIFEST 是 Markdown，供人阅读
             f"- Model: `{provider}:{model}`",
             f"- Task: {task}", "", "## Files created"]
    lines += [f"- `{p.relative_to(folder)}` ({p.stat().st_size} bytes)" for p in files] or ["- (none)"]  # 每个产物 + 大小
    if run is not None:  # 有自动运行结果就附上
        entry, code, out, secs = run
        status = "still running (interactive?)" if code is None else f"exit {code}"
        lines += ["", f"## Auto-run: `python3 {entry}` — {status} in {secs}s", "",
                  "```", out[:2000], "```"]  # 输出截到 2000 字符
    (folder / "MANIFEST.md").write_text("\n".join(lines) + "\n")
