"""确定性评估——委派编程工作区（waku.tools.workspace）。

pi 写出的脚本必须落到带日期、有文档的目录里，而不是临时目录，并自动运行。
这些测试固化：带日期的目录布局、入口脚本检测、一次真实的自动运行（一个我们
可控的小脚本）、manifest，以及禁用开关。"""

from __future__ import annotations

from datetime import datetime

from waku.tools import workspace as ws


def test_run_folder_is_dated_and_named(tmp_path, monkeypatch):
    monkeypatch.setenv("WAKU_WORKSPACE", str(tmp_path))
    when = datetime(2026, 7, 19, 12, 48, 31)
    folder = ws.new_run_folder("kimi-k3", "build me a snake game", now=when)
    assert folder.parent.name == "2026-07-19"          # 带日期的目录
    assert folder.name == "124831-kimi-k3-build-me-a-snake"  # 时间-模型-片段
    assert folder.is_dir()


def test_autorun_runs_the_entry_and_captures_output(tmp_path, monkeypatch):
    monkeypatch.setenv("WAKU_WORKSPACE", str(tmp_path))
    folder = ws.new_run_folder("m", "print hello", now=datetime(2026, 7, 19, 1, 2, 3))
    (folder / "main.py").write_text("print('hello from the script')\n")
    entry, code, out, secs = ws.autorun(folder)
    assert entry == "main.py" and code == 0
    assert "hello from the script" in out
    assert (folder / "run.log").exists()


def test_autorun_picks_main_over_a_helper(tmp_path, monkeypatch):
    monkeypatch.setenv("WAKU_WORKSPACE", str(tmp_path))
    folder = ws.new_run_folder("m", "t", now=datetime(2026, 7, 19, 1, 2, 4))
    (folder / "helper.py").write_text("X = 1\n")
    (folder / "main.py").write_text("print('main ran')\n")
    entry, code, out, _ = ws.autorun(folder)
    assert entry == "main.py" and "main ran" in out


def test_autorun_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WAKU_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("WAKU_DELEGATE_AUTORUN", "0")
    folder = ws.new_run_folder("m", "t", now=datetime(2026, 7, 19, 1, 2, 5))
    (folder / "main.py").write_text("print('hi')\n")
    assert ws.autorun(folder) is None


def test_autorun_none_when_nothing_runnable(tmp_path, monkeypatch):
    monkeypatch.setenv("WAKU_WORKSPACE", str(tmp_path))
    folder = ws.new_run_folder("m", "t", now=datetime(2026, 7, 19, 1, 2, 6))
    (folder / "notes.txt").write_text("no python here\n")
    assert ws.autorun(folder) is None


def test_manifest_documents_the_run(tmp_path, monkeypatch):
    monkeypatch.setenv("WAKU_WORKSPACE", str(tmp_path))
    folder = ws.new_run_folder("kimi-k3", "make a game", now=datetime(2026, 7, 19, 1, 2, 7))
    (folder / "game.py").write_text("print('ok')\n")
    files = ws.created_files(folder)
    run = ws.autorun(folder)
    ws.write_manifest(folder, "kimi", "kimi-k3", "make a game", files, run)
    text = (folder / "MANIFEST.md").read_text()
    assert "kimi:kimi-k3" in text and "make a game" in text
    assert "game.py" in text and "Auto-run" in text
