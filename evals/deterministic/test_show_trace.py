"""确定性评估——追踪记录在终端中仍保持可读。"""

from __future__ import annotations

from rich.console import Console

from waku.ops.show_trace import render_trace


def test_trace_renders_an_indented_timeline(tmp_path):
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        '{"type":"turn_start","ts":"2026-07-17T10:00:00+00:00","user_message":"Book lunch"}\n'
        '{"type":"gate","ts":"2026-07-17T10:00:01+00:00","decision":"allow","reason":"safe"}\n'
        '{"type":"tool","ts":"2026-07-17T10:00:02+00:00","tool":"create_event",'
        '"args":{"title":"Lunch"},"output":"created"}\n'
        '{"type":"turn_end","ts":"2026-07-17T10:00:03+00:00","reply":"Done",'
        '"iterations":1}\n',
        encoding="utf-8",
    )
    console = Console(record=True, force_terminal=False, width=200)

    assert render_trace(trace, console) == 4

    lines = console.export_text().splitlines()
    assert "Trace" in lines[0]
    assert "10:00:00 turn start · Book lunch" in lines[1]
    assert "  10:00:01 gate · allow — safe" in lines[2]
    assert "  10:00:02 tool · create_event({\"title\": \"Lunch\"}) → created" in lines[3]
    assert "10:00:03 turn end · Done · 1 iteration(s)" in lines[4]


def test_a_turn_that_never_ended_does_not_indent_the_rest_of_the_day(tmp_path):
    """崩溃的轮次不写入 turn_end；后续轮次仍须从最左对齐开始。"""
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        '{"type":"turn_start","ts":"2026-07-17T10:00:00+00:00","user_message":"Crashes"}\n'
        '{"type":"gate","ts":"2026-07-17T10:00:01+00:00","decision":"retrieve"}\n'
        '{"type":"turn_start","ts":"2026-07-17T11:00:00+00:00","user_message":"Next"}\n'
        '{"type":"gate","ts":"2026-07-17T11:00:01+00:00","decision":"skip"}\n'
        '{"type":"turn_end","ts":"2026-07-17T11:00:02+00:00","reply":"Done",'
        '"iterations":1}\n',
        encoding="utf-8",
    )
    console = Console(record=True, force_terminal=False, width=200)

    assert render_trace(trace, console) == 5

    lines = console.export_text().splitlines()
    assert lines[3].startswith("11:00:00 turn start")  # 未被崩溃向右推挤
    assert lines[4].startswith("  11:00:01 gate")
    assert lines[5].startswith("11:00:02 turn end")


def test_missing_trace_is_reported_without_error(tmp_path):
    console = Console(record=True, force_terminal=False)

    assert render_trace(tmp_path / "missing.jsonl", console) == 0

    assert "No trace file found" in console.export_text()
