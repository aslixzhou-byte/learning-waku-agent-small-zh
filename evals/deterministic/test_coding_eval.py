"""确定性评估——跨模型编程运行器（waku.ops.coding_eval）。

密闭测试中无法调用真实模型，因此我们把 pi 桩替换为退出码为 0 的空操作，
让预置的文件 + 真实的 `verify` 命令来决定判定结果。这覆盖了除模型之外的
一切：文件预置、提供方/密钥守卫，以及——最关键的一点——得分取决于
verify 的退出码，而不是 pi 的输出文本。
"""

from __future__ import annotations

import sys

from waku.ops import coding_eval as ce


def _stub_pi(monkeypatch, tmp_path):
    """shutil.which('pi') → 一个忽略 argv 并以 0 退出的平台空操作。"""
    if sys.platform == "win32":
        noop = tmp_path / "fake_pi.cmd"
        noop.write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
        target = str(noop)
    else:
        import shutil

        target = shutil.which("true") or "/usr/bin/true"
    monkeypatch.setattr(ce.shutil, "which", lambda name: target)


def _py_verify(code: str) -> str:
    """使用当前解释器构造 verify 命令（Windows 上通常没有真正的 python3）。"""
    return f"{sys.executable} -c \"{code}\""


def test_verify_pass_is_the_verdict(tmp_path, monkeypatch):
    _stub_pi(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    case = {
        "id": "ok",
        "input": "n/a",
        "files": {
            "fizzbuzz.py": (
                "def fizzbuzz(n):\n"
                "    return 'FizzBuzz' if n%15==0 else 'Fizz' if n%3==0 else "
                "'Buzz' if n%5==0 else str(n)\n"
            )
        },
        "verify": _py_verify(
            "from fizzbuzz import fizzbuzz; assert fizzbuzz(15)=='FizzBuzz'; print('ok')"
        ),
    }
    passed, why, secs = ce.run_coding_case("anthropic", "claude-opus-4-8", case)
    assert passed and why == "tests pass"


def test_verify_fail_when_code_is_wrong(tmp_path, monkeypatch):
    _stub_pi(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    case = {
        "id": "bad",
        "input": "n/a",
        "files": {"fizzbuzz.py": "def fizzbuzz(n):\n    return 'wrong'\n"},
        "verify": _py_verify("from fizzbuzz import fizzbuzz; assert fizzbuzz(3)=='Fizz'"),
    }
    passed, why, _ = ce.run_coding_case("anthropic", "claude-opus-4-8", case)
    assert not passed  # pi “运行”了，但代码未通过 verify


def test_missing_key_is_reported(tmp_path, monkeypatch):
    _stub_pi(monkeypatch, tmp_path)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    passed, why, _ = ce.run_coding_case("xai", "grok-4.5", {"id": "x", "input": "n/a"})
    assert not passed and "api key" in why


def test_unmapped_provider_is_reported(tmp_path, monkeypatch):
    _stub_pi(monkeypatch, tmp_path)
    passed, why, _ = ce.run_coding_case("nope", "whatever", {"id": "x", "input": "n/a"})
    assert not passed and "provider mapping" in why


def test_every_pinned_provider_maps_to_a_pi_provider():
    # 竞技场固定的提供方必须都能映射到 pi，否则编程轮次无法运行它们
    for prov in ("anthropic", "openai", "gemini", "kimi", "xai", "glm"):
        assert prov in ce.PI_PROVIDER


def test_coding_cases_load_and_have_verify():
    cases = ce.load_coding_cases()
    assert len(cases) >= 2
    for c in cases:
        assert "id" in c and "input" in c and "verify" in c


def test_coding_case_for_message_matches_trimmed():
    cases = [{"id": "fizz", "input": "Create fizzbuzz.py"}]
    assert ce.coding_case_for_message("  Create fizzbuzz.py ", cases)["id"] == "fizz"
    assert ce.coding_case_for_message("build a website", cases) is None


def test_stream_runner_scores_by_verify_and_emits_lines(tmp_path, monkeypatch):
    _stub_pi(monkeypatch, tmp_path)  # pi = 空操作；由预置文件提供代码
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    lines = []
    passed, why, secs = ce.run_coding_stream(
        "anthropic",
        "claude-opus-4-8",
        task="n/a",
        files={
            "fizzbuzz.py": (
                "def fizzbuzz(n):\n    return 'FizzBuzz' if n%15==0 else str(n)\n"
            )
        },
        verify=_py_verify(
            "from fizzbuzz import fizzbuzz; assert fizzbuzz(15)=='FizzBuzz'"
        ),
        on_line=lines.append,
    )
    assert passed is True and why == "tests pass"
    assert any(ln.startswith("$ pi") for ln in lines)  # 启动命令行已流式输出


def test_stream_runner_free_form_has_no_verdict(tmp_path, monkeypatch):
    _stub_pi(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    passed, why, _ = ce.run_coding_stream(
        "anthropic",
        "claude-opus-4-8",
        task="build snake",
        files=None,
        verify=None,
        on_line=lambda _ln: None,
    )
    assert passed is None  # 没有可评分的对象 -> 无通过/失败，只是运行了
