"""跨模型编码评测
不做解释说明
"""

from __future__ import annotations

import json        # 解析编码用例的每一行（每行一个 JSON 对象）
import os          # 读 API 密钥，并作为子进程环境传给 pi
import shutil      # 定位 pi 可执行文件（shutil.which）
import subprocess  # 启动 pi 与 verify 命令
import tempfile    # 为每个用例创建一次性工作目录（沙箱）
import threading   # 看门狗：定时杀掉超时的 pi 进程
import time        # 计时每次运行的耗时
from pathlib import Path

from waku.loop.models import PROVIDERS   # 模型提供方注册表（用于查密钥环境变量）

# 编码用例文件的位置：仓库根的 evals/coding.jsonl
_CODING = Path(__file__).resolve().parents[2] / "evals" / "coding.jsonl"

# Waku 模型提供方 id -> pi 内置的模型提供方 id（见 `pi --list-models`）。
# 两侧的命名并不一致，必须显式映射，否则 pi 不认识这个提供方。
PI_PROVIDER = {
    "anthropic": "anthropic", "openai": "openai", "gemini": "google",
    "kimi": "moonshotai", "xai": "xai", "glm": "zai",
    "deepseek": "deepseek", "minimax": "minimax", "openrouter": "openrouter",
}


def load_coding_cases() -> list[dict]:
    """按文件顺序返回全部编码用例；文件缺失时返回空列表。"""
    if not _CODING.exists():
        return []
    return [json.loads(line) for line in _CODING.read_text().splitlines() if line.strip()]
    # 每一非空行就是一个用例 dict；跳过空行，避免 json.loads 崩溃

def pi_available() -> bool:
    return shutil.which("pi") is not None  # 检查 PATH 上是否存在 pi 可执行文件

def coding_case_for_message(message: str, cases: list[dict] | None = None) -> dict | None:
    """输入与该提示词匹配（去除首尾空白后的精确匹配）的编码用例，这样Compare就知道要
    预置哪些文件、并按它的 `verify` 来评分。对自由形式的编码提示词（如「写个贪吃蛇
    并跑起来」）返回 None，pi 仍然会运行并流式输出，只是没有可用来评分的测试。"""
    msg = (message or "").strip()               # 归一化输入；空消息按空串处理
    for c in (cases if cases is not None else load_coding_cases()):   # 没传列表就自己加载
        if (c.get("input") or "").strip() == msg:   # 与用例的 input 精确匹配（同样去空白）
            return c
    return None                                 # 没有匹配 = 自由形式提示词，无需预置文件

def run_coding_stream(provider: str, model: str, task: str, files: dict | None,
                      verify: str | None, on_line, timeout: int = 300) -> tuple:
    """与 run_coding_case 类似，但会把 pi 的 stdout 逐行流式传给 `on_line`，
    这样Compare可以实时展示终端在执行任务。返回 (passed, why, seconds)；当没有
    verify 时 `passed` 为 None（自由形式的提示词只是运行，没有可评分的东西）。"""
    pi_bin = shutil.which("pi")                 # 定位 pi；找不到就提前失败
    if not pi_bin:
        on_line("pi is not installed")          # 把失败原因实时推给 UI
        return (False, "pi not installed", 0.0)
    pi_prov = PI_PROVIDER.get(provider)         # Waku 提供方 id -> pi 提供方 id
    if not pi_prov:
        return (False, f"pi has no provider mapping for '{provider}'", 0.0)
    key = _key_for(provider)                    # 从环境变量取该提供方的 API 密钥
    if not key:
        prov = PROVIDERS.get(provider)          # 回查注册表以获得密钥环境变量名
        return (False, f"no api key ({prov.key_env if prov else provider})", 0.0)

    workdir = Path(tempfile.mkdtemp(prefix=f"code-{provider}-"))  # 一次性沙箱目录
    for name, content in (files or {}).items(): # 把用例要求的文件预置进工作目录
        (workdir / name).write_text(content)
    on_line(f"$ pi --provider {pi_prov} --model {model} -p …")   # 回显即将运行的命令

    t0 = time.perf_counter()                    # 开始计时
    try:
        proc = subprocess.Popen(                # 异步启动 pi（不阻塞调用方）
            [pi_bin, "--provider", pi_prov, "--model", model, "--api-key", key,
             "-p", task, "-a", "--no-session"],
            cwd=workdir, stdin=subprocess.DEVNULL,   # 服务器环境下没有 TTY：pi 不能
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,  # 阻塞等待 stdin
            text=True, bufsize=1, env=os.environ.copy())      # 文本 + 行缓冲，继承环境
    except OSError as exc:                      # 启动失败（权限 / 路径等）
        return (False, f"couldn't launch pi: {exc}", round(time.perf_counter() - t0, 1))

    killer = threading.Timer(timeout, proc.kill)   # 看门狗：杀掉挂死的 pi
    killer.start()                              # 开始倒计时
    try:
        for line in proc.stdout:                # 逐行阻塞读取，直到 pi 退出
            on_line(line.rstrip("\n"))          # 实时转发每一行给 UI
        proc.wait()                             # 等 pi 退出（回收资源）
    finally:
        killer.cancel()                         # 正常结束就取消看门狗
    secs = round(time.perf_counter() - t0, 1)   # 记录总耗时

    if not verify:                              # 自由形式任务没有测试可跑
        on_line("[done — no test to score]")
        return (None, "ran (no test)", secs)
    try:
        v = subprocess.run(verify, shell=True, cwd=workdir, capture_output=True,
                           text=True, timeout=120, check=False)   # 在沙箱里跑 verify
    except subprocess.TimeoutExpired:           # verify 卡死也算失败
        on_line("[verify timed out]")
        return (False, "verify timed out", secs)
    if v.returncode == 0:                       # 退出码 0 = 测试通过（SWE-bench 风格）
        on_line("[verify] tests pass")
        return (True, "tests pass", secs)
    tail = (v.stdout or v.stderr).strip().splitlines()   # 取输出最后一行作为失败原因
    why = tail[-1][:120] if tail else "tests failed"    # 截断到 120 字符以便展示
    on_line(f"[verify] FAILED — {why}")
    return (False, why, secs)


def _key_for(provider: str) -> str:
    prov = PROVIDERS.get(provider)              # 查该提供方的配置
    return os.getenv(prov.key_env, "") if prov else ""  # 从密钥环境变量取值，缺省空串


def run_coding_case(provider: str, model: str, case: dict,
                    timeout: int = 300) -> tuple[bool, str, float]:
    """在 (provider, model) 上通过 pi 运行一个编码用例，然后按该用例的 `verify`
    命令评分。返回 (passed, why, seconds)。
    裁决依据是 `verify` 在 pi 工作的那个沙箱里运行的退出码——所以「passed」意味着
    代码确实做到了要求的事，无论模型是怎么做到的。这里不会信任 pi 的任何文字描述。"""
    pi_bin = shutil.which("pi")                 # 定位 pi；找不到就提前失败
    if not pi_bin:
        return (False, "pi not installed", 0.0)
    pi_prov = PI_PROVIDER.get(provider)         # 映射到 pi 认识的提供方 id
    if not pi_prov:
        return (False, f"pi has no provider mapping for '{provider}'", 0.0)
    key = _key_for(provider)                    # 取 API 密钥
    if not key:
        prov = PROVIDERS.get(provider)          # 查密钥环境变量名
        return (False, f"no api key ({prov.key_env if prov else provider})", 0.0)

    workdir = Path(tempfile.mkdtemp(prefix=f"code-{provider}-"))  # 一次性沙箱
    for name, content in (case.get("files") or {}).items():       # 预置用例要求的文件
        (workdir / name).write_text(content)

    t0 = time.perf_counter()                    # 开始计时
    try:
        # -a 信任项目本地文件；--no-session 让这次运行保持临时性。
        subprocess.run(
            [pi_bin, "--provider", pi_prov, "--model", model, "--api-key", key,
             "-p", case["input"], "-a", "--no-session"],
            cwd=workdir, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, timeout=timeout, check=False)   # 阻塞等待 pi 完成
    except subprocess.TimeoutExpired:           # 超过 timeout 秒视为超时失败
        return (False, f"pi timed out after {timeout}s", round(time.perf_counter() - t0, 1))
    except OSError as exc:                      # pi 二进制本身无法启动
        return (False, f"couldn't launch pi: {exc}", round(time.perf_counter() - t0, 1))

    # 我们不以 pi 自己的退出码作为门禁——非零退出也可能已经写出了可用的代码。
    # 唯一有意义的裁判是 verify 命令。
    verify = case.get("verify")                 # 用例可选地指定评分命令
    if not verify:                              # 没给 verify = 无测试可跑，视为干净通过
        return (True, "no verify (ran clean)", round(time.perf_counter() - t0, 1))
    try:
        v = subprocess.run(verify, shell=True, cwd=workdir, capture_output=True,
                           text=True, timeout=120, check=False)   # 在沙箱里跑 verify
    except subprocess.TimeoutExpired:           # verify 卡死也算失败
        return (False, "verify timed out", round(time.perf_counter() - t0, 1))
    secs = round(time.perf_counter() - t0, 1)   # 记录总耗时
    if v.returncode == 0:                       # 退出码 0 = 测试通过
        return (True, "tests pass", secs)
    tail = (v.stdout or v.stderr).strip().splitlines()   # 取最后一行输出作为原因
    return (False, (tail[-1][:120] if tail else "tests failed"), secs)
