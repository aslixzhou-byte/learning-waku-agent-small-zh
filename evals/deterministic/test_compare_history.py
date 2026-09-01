"""确定性评估——Compare自己的历史日志。

Compare是基准测试而非对话，因此它的运行记录绝不能写入代理的真实状态
（chat_log / facts / calendar）。它们存放在自己限长封顶的 JSONL 文件中，
按模型的记分牌就是对它的扫描。这些测试固化了存储契约：追加 + 裁剪、
回复截断的隔离性，以及聚合计算。"""

from __future__ import annotations

from waku.ops import compare_history as ch


def _result(spec, model, latency, tin, tout, cost, error=None):
    return {"spec": spec, "provider": spec.split(":")[0], "model": model,
            "latency_ms": latency, "tokens_in": tin, "tokens_out": tout,
            "cost_usd": cost, "iterations": 1, "gate": {"decision": "skip"},
            "tools": [{"tool": "create_event"}], "error": error, "reply": "ok"}


def test_append_and_load_round_trip(tmp_path):
    ch.append_run(tmp_path, "hi", [_result("kimi:kimi-k3", "kimi-k3", 1000, 10, 5, 0.01)])
    runs = ch.load_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0]["message"] == "hi"
    r = runs[0]["results"][0]
    assert r["model"] == "kimi-k3" and r["gate"] == "skip" and r["tools"] == ["create_event"]


def test_it_writes_to_its_own_file_not_state_db(tmp_path):
    ch.append_run(tmp_path, "hi", [_result("a:b", "b", 1, 1, 1, 0.0)])
    assert (tmp_path / "compare" / "history.jsonl").exists()
    assert not (tmp_path / "state.db").exists()   # 绝不触碰代理的数据库


def test_clear_wipes_only_the_history(tmp_path):
    ch.append_run(tmp_path, "hi", [_result("a:b", "b", 1, 1, 1, 0.0)])
    (tmp_path / "state.db").write_text("real data")   # 一个必须存活下来的同级文件
    ch.clear(tmp_path)
    assert ch.load_runs(tmp_path) == []
    assert not (tmp_path / "compare" / "history.jsonl").exists()
    assert (tmp_path / "state.db").read_text() == "real data"   # 未受影响


def test_history_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "MAX_RUNS", 3)
    for i in range(5):
        ch.append_run(tmp_path, f"run {i}", [_result("a:b", "b", 1, 1, 1, 0.0)])
    runs = ch.load_runs(tmp_path)
    assert [r["message"] for r in runs] == ["run 2", "run 3", "run 4"]   # 最旧的已被淘汰


def test_reply_is_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "REPLY_CAP", 10)
    big = {**_result("a:b", "b", 1, 1, 1, 0.0), "reply": "x" * 500}
    ch.append_run(tmp_path, "hi", [big])
    assert len(ch.load_runs(tmp_path)[0]["results"][0]["reply"]) == 10


def test_aggregate_totals_over_successful_runs_only(tmp_path):
    runs = [
        {"message": "q1", "results": [_result("k:m", "m", 1000, 100, 100, 0.02),
                                      _result("g:n", "n", 2000, 200, 200, 0.04, error="boom")]},
        {"message": "q2", "results": [_result("k:m", "m", 3000, 300, 300, 0.06)]},
    ]
    agg = {a["spec"]: a for a in ch.aggregate(runs)}
    m = agg["k:m"]
    assert m["runs"] == 2 and m["ok"] == 2
    assert m["total_latency_ms"] == 4000 and m["total_cost_usd"] == 0.08
    # token 拆分输入/输出后仍然汇总；辅助函数每次运行发送 tin=tout（100+300）
    assert m["total_tokens_in"] == 400 and m["total_tokens_out"] == 400
    assert m["total_tokens"] == 800 == m["total_tokens_in"] + m["total_tokens_out"]
    n = agg["g:n"]
    assert n["runs"] == 1 and n["ok"] == 0          # 出错的运行被计入，但不对总量做贡献
    assert n["total_cost_usd"] == 0.0
