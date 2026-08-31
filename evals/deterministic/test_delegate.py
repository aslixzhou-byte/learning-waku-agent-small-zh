"""确定性评估——delegate_task 把编程任务如实交给 pi 处理。

密闭：pi 从不会被真正启动。subprocess.run 和 shutil.which 被 monkeypatch
替换，所以这些测试在无 node、无网络的环境中也能运行（包括 CI）。
固化的契约：pi 以无头模式运行在循环自己的模型上、outbox 纸质痕迹、以及每种
失败模式的诚实提示语（未安装 / 超时 / 工作目录错误）。"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from evals.helpers import ScriptedClient, make_waku, response, text_block, tool_block
from waku.config import Settings
from waku.tools import experimental


import pytest


@pytest.fixture(autouse=True)
def _tmp_workspace(tmp_path, monkeypatch):
    """绝不让 delegate 测试写入仓库的 ./waku_workspace。"""
    monkeypatch.setenv("WAKU_WORKSPACE", str(tmp_path / "ws"))


def fake_run(record, stdout="Done. Created hello.py.", returncode=0):
    def run(argv, **kwargs):
        record["argv"] = argv
        record["kwargs"] = kwargs
        return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)
    return run


def test_delegate_task_invokes_pi_print_mode(tmp_path, monkeypatch):
    """完整循环接线：模型调用 delegate_task → pi 触发 → 一个草稿任务落到带日期的
    工作目录中，附带 MANIFEST + pi 记录，pi 的回答通过工具结果返回。"""
    record = {}
    monkeypatch.setenv("WAKU_EXPERIMENTAL", "1")
    monkeypatch.setenv("WAKU_WORKSPACE", str(tmp_path / "ws"))   # 让它远离仓库目录
    monkeypatch.setattr(experimental.shutil, "which", lambda _: "/fake/bin/pi")
    monkeypatch.setattr(experimental.subprocess, "run", fake_run(record))

    gate = response([text_block('{"retrieve": false, "query": "", "reason": "test"}')])
    script = [gate] + [
        response([tool_block("delegate_task", {"task": "create hello.py"})], "tool_use"),
        response([text_block("pi handled it.")]),
    ]
    app = make_waku(tmp_path / "home", client=ScriptedClient(script))
    result = app.respond("have pi create hello.py")

    assert [c["tool"] for c in result.tool_calls] == ["delegate_task"]
    argv = record["argv"]
    assert argv[0] == "/fake/bin/pi"
    assert "-p" in argv and "create hello.py" in argv
    assert "-a" in argv and "--no-session" in argv          # 无头、非交互
    output = result.tool_calls[0]["output"]
    assert "Done. Created hello.py." in output and "saved to" in output.lower()
    # 运行结果落在带日期的工作目录中，含 manifest + 记录
    manifests = list((tmp_path / "ws").rglob("MANIFEST.md"))
    assert len(manifests) == 1 and "create hello.py" in manifests[0].read_text()
    assert list((tmp_path / "ws").rglob("pi-transcript.log"))


def test_delegate_runs_pi_on_the_calling_model(tmp_path, monkeypatch):
    """子代理使用循环自己的大脑编程——delegate_task 把当前模型的
    provider/model/key 传给 pi，因此按模型的竞速实际上是在比较模型
    （kimi 的 pi 用 kimi，opus 的 pi 用 opus）。"""
    record = {}
    monkeypatch.setattr(experimental.shutil, "which", lambda _: "/fake/bin/pi")
    monkeypatch.setattr(experimental.subprocess, "run", fake_run(record))
    monkeypatch.setenv("MOONSHOT_API_KEY", "k")
    tool = experimental.make_delegate_tool(Settings(home=tmp_path, provider="kimi", model="kimi-k3"))
    tool.fn(task="write fizzbuzz")
    argv = record["argv"]
    assert "--provider" in argv and "moonshotai" in argv    # kimi -> pi 的 moonshotai
    assert "--model" in argv and "kimi-k3" in argv
    assert "--api-key" in argv


def test_delegate_without_pi_returns_install_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(experimental.shutil, "which", lambda _: None)
    tool = experimental.make_delegate_tool(Settings(home=tmp_path))
    out = tool.fn(task="anything")
    assert experimental.PI_INSTALL_HINT in out
    assert "isn't installed" in out


def test_delegate_timeout_is_honest(tmp_path, monkeypatch):
    monkeypatch.setattr(experimental.shutil, "which", lambda _: "/fake/bin/pi")

    def run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 300))

    monkeypatch.setattr(experimental.subprocess, "run", run)
    tool = experimental.make_delegate_tool(Settings(home=tmp_path))
    out = tool.fn(task="huge refactor", timeout_seconds=7)
    assert "7s" in out and "WAKU_DELEGATE_TIMEOUT" in out


def test_delegate_rejects_missing_cwd_and_empty_task(tmp_path, monkeypatch):
    monkeypatch.setattr(experimental.shutil, "which", lambda _: "/fake/bin/pi")
    tool = experimental.make_delegate_tool(Settings(home=tmp_path))
    assert "doesn't exist" in tool.fn(task="fix tests", cwd=str(tmp_path / "nope"))
    assert "needs a 'task'" in tool.fn()   # 空模型调用 → 恢复提示文本，不抛异常


def test_delegate_failure_surfaces_stderr(tmp_path, monkeypatch):
    monkeypatch.setattr(experimental.shutil, "which", lambda _: "/fake/bin/pi")

    def run(argv, **kwargs):
        return SimpleNamespace(stdout="", stderr="No API key found", returncode=1)

    monkeypatch.setattr(experimental.subprocess, "run", run)
    tool = experimental.make_delegate_tool(Settings(home=tmp_path))
    out = tool.fn(task="anything")
    assert "pi hit an error" in out and "No API key found" in out


def test_experimental_flag_gates_registration(tmp_path, monkeypatch):
    """演示依赖这一点：关闭标志 → 无 delegate_task；开启标志 → 存在。"""
    monkeypatch.delenv("WAKU_EXPERIMENTAL", raising=False)
    app_off = make_waku(tmp_path / "off", client=ScriptedClient([]))
    assert "delegate_task" not in app_off.tools._tools

    monkeypatch.setenv("WAKU_EXPERIMENTAL", "1")
    app_on = make_waku(tmp_path / "on", client=ScriptedClient([]))
    assert "delegate_task" in app_on.tools._tools
    assert "run_command" in app_on.tools._tools   # 骨架工具仍然注册
