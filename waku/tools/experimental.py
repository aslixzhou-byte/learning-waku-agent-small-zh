"""路线图工具

不做解释说明
"""

from __future__ import annotations  # 让类型注解在旧版 Python 里也能用

import os         # 读取 WAKU_DELEGATE_TIMEOUT 环境变量
import shutil     # which() 在 PATH 里查找 pi 可执行文件
import subprocess # 以无头模式运行 pi
from datetime import datetime  # 生成发件箱日志文件名的时间戳
from pathlib import Path       # 处理路径

from waku.config import Settings     # 应用配置对象
from waku.tools.registry import Tool # 工具定义类

PI_INSTALL_HINT = "npm install -g --ignore-scripts @earendil-works/pi-coding-agent"

PLANNED = [
    {"name": "run_command", "box": "Terminal tool",
     # 提示词：在沙箱中运行 shell 命令并读取输出——Hermes 的 'Terminal' 工具。
     # 需要先有真正的沙箱 + 安全面。
     "description": "Run a shell command in a sandbox and read the output — Hermes's 'Terminal' "
                    "tool. Needs a real sandbox + safety surface first."},
    {"name": "browse_web", "box": "Browser tool",
     # 提示词：打开一个页面并读取/点击它——Hermes 的 'Browser' 工具。
     # （search_web 已经覆盖了只读的网页查询。）
     "description": "Open a page and read/click it — Hermes's 'Browser' tool. (search_web already "
                    "covers read-only web lookups.)"},
    {"name": "schedule_task", "box": "Cron Job",
     # 提示词：让代理自己安排循环运行的日程。如今 `make brief` + 系统 cron 行
     # 已经能定时运行；这个功能会把它搬到应用内。
     "description": "Let the agent schedule its own recurring runs. Today `make brief` + a system "
                    "cron line already does scheduled runs; this would move it in-app."},
]


def make_delegate_tool(settings: Settings) -> Tool:
    """子代理盒子，真正接通：把一个编码任务委托给 pi。
    与每个 Waku 工具相同的诚实契约——返回字符串精确说明发生了什么
    （完成 / 失败 / 超时 / 未安装 pi），足够短，让语音网关能朗读。
    完整的 pi 转录会放进发件箱。
    """

    def delegate_task(task: str = "", cwd: str = "", timeout_seconds: int = 0) -> str:
        if not task.strip():  # 空任务直接拒绝，引导模型给出自包含描述
            return ("delegate_task needs a 'task' — a plain-English description of the "
                    "coding job, e.g. 'fix the failing test in this repo'.")
        pi_bin = shutil.which("pi")  # 在 PATH 里找 pi 可执行文件
        if not pi_bin:  # 没装 pi：如实告知并给出安装命令
            return f"pi isn't installed, so I can't delegate. Install it with: {PI_INSTALL_HINT}"

        from waku.tools import workspace  # 局部导入：只在需要工作区能力时才加载
        if cwd:
            workdir = Path(cwd).expanduser()  # 展开 ~ 为用户主目录
            if not workdir.is_dir():
                return f"delegate_task: the working directory '{cwd}' doesn't exist."
            in_workspace = False   # 在用户自己的项目里工作；不要移动/自动运行
        else:
            # 无仓库的任务：放到一个带日期、有文档记录的工作区文件夹里，
            # 这样脚本能留存并可追踪（不是临时目录），然后自动运行。
            workdir = workspace.new_run_folder(settings.model or settings.provider, task)
            in_workspace = True

        timeout = int(timeout_seconds) or int(os.getenv("WAKU_DELEGATE_TIMEOUT", "300"))  # 超时：参数优先，否则读环境变量
        # 用循环正在用的同一个"大脑"运行 pi，这样子代理的编码就是
        # 这个模型的编码（这正是逐模型对比的意义）。pi 原生支持我们固定的
        # 每个模型提供方；如果这个提供方无法映射，则回退到 pi 自己的默认值。
        # -a/--no-session = 无头模式；stdin=DEVNULL 让 pi 永远不会
        # 卡在一个它在服务器下并不拥有的 TTY 上。
        from waku.ops.coding_eval import PI_PROVIDER, _key_for  # 复用评估用的提供方映射表
        cmd = [pi_bin]  # 命令以 pi 本身开头
        pi_prov = PI_PROVIDER.get(settings.provider)  # 查这个提供方在 pi 里的名字
        if pi_prov and settings.model:  # 能映射到 pi 提供方且有模型：显式传参
            cmd += ["--provider", pi_prov, "--model", settings.model]
            key = _key_for(settings.provider)  # 尝试取该提供方的 API key
            if key:  # 有 key 才传；没有则交给 pi 自己的凭据
                cmd += ["--api-key", key]
        cmd += ["-p", task, "-a", "--no-session"]  # 无头打印模式 + 无会话
        try:
            result = subprocess.run(cmd, cwd=workdir, stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:  # 超时：明确说出已停止，并给出调大超时的方法
            return (f"pi was still working after {timeout}s so I stopped it — try a smaller "
                    f"task, or raise WAKU_DELEGATE_TIMEOUT.")
        except OSError as exc:  # 启动失败
            return f"Couldn't launch pi: {exc}"

        # 完整的 pi 转录：跟着工作放在工作区，或放在发件箱里。
        transcript = (workdir / "pi-transcript.log") if in_workspace else (  # 工作区任务 → 转录留在工作区
            settings.home / "outbox" / f"delegate-{datetime.now():%Y%m%d-%H%M%S}.log")  # 否则放进发件箱
        transcript.parent.mkdir(parents=True, exist_ok=True)  # 确保父目录存在
        transcript.write_text(f"$ {' '.join(cmd[:-4])} -p {task!r}   (cwd: {workdir})\n\n"  # 命令 + 输出全量落盘
                              f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}",
                              encoding="utf-8")

        if result.returncode != 0:  # pi 报错：截取 stderr 尾部 + 完整日志路径
            err = (result.stderr or result.stdout).strip()[-200:] or "no output"
            return f"pi hit an error: {err} (full log: {transcript})"
        summary = result.stdout.strip()[-500:] or "(pi finished but printed nothing)"  # 成功：取 stdout 尾部作摘要

        if not in_workspace:  # 用户自己的项目：不自动跑，只回报结果
            return f"pi finished the delegated task in {workdir}.\n{summary}\n(full log: {transcript})"

        # 临时任务：记录这次运行（带日期的 MANIFEST）并自动运行脚本，
        # 把运行结果回传给循环，这样模型能对结果作出反应。
        files = workspace.created_files(workdir)  # 收集 pi 写出的文件
        run = workspace.autorun(workdir)  # 自动运行入口脚本
        workspace.write_manifest(workdir, settings.provider, settings.model or "(default)", task, files, run)  # 写 MANIFEST 记录
        made = ", ".join(p.name for p in files[:6]) or "no files"  # 文件名摘要（最多列 6 个）
        lines = [f"pi finished. Files saved to {workdir} ({made}).", summary]
        if run is not None:  # 自动运行有结果：把退出码/耗时/输出接在末尾
            entry, code, out, secs = run
            verdict = "still running (interactive)" if code is None else ("ran clean" if code == 0 else f"exited {code}")
            lines.append(f"\nAuto-ran {entry}: {verdict} in {secs}s.\n{out[-400:]}")
        return "\n".join(lines)  # 整段文本返回给模型观察

    return Tool(
        name="delegate_task",
        description=(
            # 提示词：把一个编码任务（修测试、多文件改动、写程序）委托给 pi，
            # 一个运行在本机上的专业编码代理。给它一个自包含的任务，
            # 当工作针对现有项目时，把该项目的绝对路径作为 cwd。
            # 用这个来做真正的编程工作，而不是在聊天里描述代码。
            "Delegate a CODING task (fixing tests, multi-file edits, writing "
            "programs) to pi, a specialist coding agent running locally on this "
            "machine. Give it a self-contained task and, when the work targets an "
            "existing project, that project's absolute path as cwd. Use this for "
            "real programming work instead of describing code in chat."),
        input_schema={
            "type": "object",
            "properties": {
                "task": {"type": "string",
                         # 提示词：对编码任务的通俗语言描述，自包含
                         "description": "Plain-English description of the coding job, self-contained"},
                "cwd": {"type": "string",
                        # 提示词：要工作的仓库/目录的绝对路径；省略则用临时沙箱
                        "description": "Absolute path of the repo/directory to work in; omit for a scratch sandbox"},
                "timeout_seconds": {"type": "integer",
                                    # 提示词：允许 pi 工作的最大秒数（默认 300）
                                    "description": "Max seconds to let pi work (default 300)"},
            },
            "required": ["task"],
        },
        fn=delegate_task,
    )


def _stub(name: str, description: str, box: str) -> Tool:
    """构造一个骨架工具：返回诚实的 'coming soon'，不假装实现了什么。"""
    def fn(**kwargs) -> str:
        return (f"'{name}' maps to the '{box}' box on the architecture chart and isn't wired "
                f"in yet — it's on the roadmap (coming soon). Tell the user honestly.")

    return Tool(name=name, description=f"[coming soon] {description}",  # 描述前缀标记它是占位
                input_schema={"type": "object", "properties": {}}, fn=fn)  # 无参数 schema


def make_tools(settings: Settings) -> list[Tool]:
    """实验性工具，仅在 WAKU_EXPERIMENTAL=1 时注册：可用的 pi 委托
    以及其余的骨架工具。"""
    return [make_delegate_tool(settings)] + [  # 一个真工具 + 全部骨架
        _stub(p["name"], p["description"], p["box"]) for p in PLANNED
    ]
