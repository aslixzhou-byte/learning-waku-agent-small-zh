"""确定性评估——“会议触发了吗？”这是一个单元测试。

这里没有任何 LLM 做判定。每个用例断言一个二元的、可检查的结果：正确的工具
是否触发（或未触发）、参数是否正确、产物（数据库行 / outbox 文件）是否存在。
0 或 1。这是大多数团队跳过、却不应跳过的另一半评估。

两层：
  offline  — 脚本化模型，始终运行，测试我们的代码（循环、工具、接线）
  live     — 真实模型，当激活的提供方有密钥时运行，测试 evals/dataset.jsonl
             上的模型+提示词行为（真正的评估）
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.helpers import HAS_KEY, ScriptedClient, has_key, make_waku, response, text_block, tool_block

DATASET = [
    json.loads(line)
    for line in (Path(__file__).resolve().parents[1] / "dataset.jsonl").read_text().splitlines()
    if line.strip()
]

# ---------- 离线层：我们的管道无需任何模型即可确定性测试


def test_create_event_writes_db_and_ics(tmp_path):
    gate = response([text_block('{"retrieve": false, "query": "", "reason": "test"}')])
    turn = [
        response([tool_block("create_event", {"title": "Coffee with Alex", "start": "2026-07-14T09:00"})], "tool_use"),
        response([text_block("Booked!")]),
    ]
    app = make_waku(tmp_path / "home", client=ScriptedClient([gate] + turn))
    result = app.respond("coffee with alex tuesday 9am")

    assert [c["tool"] for c in result.tool_calls] == ["create_event"]
    row = app.conn.execute("SELECT title, start FROM calendar_events").fetchone()
    assert row["title"] == "Coffee with Alex"
    assert row["start"] == "2026-07-14T09:00"
    assert "SUMMARY:Coffee with Alex" in (tmp_path / "home" / "calendar.ics").read_text()


def test_create_event_is_idempotent(tmp_path):
    """回归测试：第一次真实测试把会议重复预订了三次——模型在后续轮次里重跑了
    create_event。相同的 title+start 绝不能重复。"""
    gate = response([text_block('{"retrieve": false, "query": "", "reason": "test"}')])
    args = {"title": "Swim with Sergey", "start": "2026-07-11T17:00"}
    script = [gate] + [
        response([tool_block("create_event", args, "tu_1"),
                  tool_block("create_event", {**args, "start": "2026-07-11T17:00:00"}, "tu_2")], "tool_use"),
        response([text_block("Booked once.")]),
    ]
    app = make_waku(tmp_path / "home", client=ScriptedClient(script))
    result = app.respond("swim with sergey saturday 5pm")

    rows = app.conn.execute("SELECT COUNT(*) FROM calendar_events").fetchone()[0]
    assert rows == 1, f"expected 1 event, got {rows}"
    assert "already exists" in result.tool_calls[1]["output"]
    ics = (tmp_path / "home" / "calendar.ics").read_text()
    assert ics.count("SUMMARY:Swim with Sergey") == 1


def test_history_records_tool_use(tmp_path):
    """回归测试的配套：下一轮的工作记忆必须显示 [tools used: ...] 这一行，
    这样模型才知道自己已经操作过了。"""
    gate = response([text_block('{"retrieve": false, "query": "", "reason": "test"}')])
    script = [gate] + [
        response([tool_block("create_event", {"title": "X", "start": "2026-07-14T09:00"})], "tool_use"),
        response([text_block("Done.")]),
    ]
    app = make_waku(tmp_path / "home", client=ScriptedClient(script))
    app.respond("book X monday 9am")
    assert "[tools used: create_event" in app.session.history[-1]["content"]


def test_no_tool_turn_ends_loop_in_one_iteration(tmp_path):
    script = [
        response([text_block('{"retrieve": false, "query": "", "reason": "test"}')]),
        response([text_block("Paris.")]),
    ]
    app = make_waku(tmp_path / "home", client=ScriptedClient(script))
    result = app.respond("capital of france?")
    assert result.reply == "Paris." and result.iterations == 1 and result.tool_calls == []


def test_iteration_guardrail_stops_runaway_loop(tmp_path):
    gate = response([text_block('{"retrieve": false, "query": "", "reason": "test"}')])
    runaway = [
        response([tool_block("save_note", {"subject": "x", "content": "y"}, f"tu_{i}")], "tool_use")
        for i in range(99)
    ]
    app = make_waku(tmp_path / "home", client=ScriptedClient([gate] + runaway), max_iterations=3)
    result = app.respond("loop forever")
    assert result.iterations == 3 and "iteration limit" in result.reply


# ---------- 实时层：对数据集运行真正的模型评估
# 标记为 `live`，以便离线时保持测试套件全绿：
#   uv run python -m pytest -q evals/deterministic -m "not live"


@pytest.mark.live
@pytest.mark.skipif(not HAS_KEY, reason="live eval needs the active provider's API key")
@pytest.mark.parametrize("case", DATASET, ids=[c["id"] for c in DATASET])
def test_dataset_case(case, tmp_path):
    if not has_key():
        pytest.skip("live eval needs the active provider's API key")
    app = make_waku(tmp_path / "home")
    if "setup_fact" in case:
        app.memory.facts.add(case["setup_fact"]["subject"], case["setup_fact"]["content"])

    result = app.respond(case["input"])
    fired = [c["tool"] for c in result.tool_calls]

    if case["expect_tool"] is None:
        assert fired == [], f"expected no tools, model called {fired}"
    else:
        assert case["expect_tool"] in fired, f"expected {case['expect_tool']}, model called {fired}"
        args = next(c["args"] for c in result.tool_calls if c["tool"] == case["expect_tool"])
        for key, needle in case.get("expect_in_args", {}).items():
            assert needle.lower() in str(args.get(key, "")).lower(), (
                f"expected '{needle}' in args[{key}], got: {args.get(key)}"
            )
        # 多工具用例（pokemon-team、worldcup-final）：循环必须真正迭代多次，
        # 而不是满足一个预期后就停止
        want = case.get("expect_min_tool_calls", 0)
        assert len(fired) >= want, f"only {len(fired)} tool calls, wanted >= {want}"
