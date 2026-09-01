"""Agent Trace 瀑布式查看器 把 .waku/traces/*.jsonl 渲染成 Phoenix 风格的时间轴瀑布。
每一「轮对话」是一张卡片，卡片内按时间轴从左到右画出各事件（门禁 / LLM 调用 /
工具调用 / 记忆整合）的耗时条，宽度 = 该动作实际耗时。
启动：
    python -m waku.ops.trace_viewer
    然后浏览器打开 http://127.0.0.1:8001
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from waku.config import load_settings

app = FastAPI(title="Waku Trace Viewer")


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def list_trace_files() -> list[dict]:
    """列出 traces 目录下所有 jsonl，最新的在前。"""
    settings = load_settings()
    traces = settings.home / "traces"
    if not traces.is_dir():
        return []
    files = sorted(traces.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.name, "date": p.stem} for p in files]


def _load_events(date: str | None) -> list[dict]:
    """读取某个日期的 trace 文件；date=None 用最新一个。"""
    settings = load_settings()
    traces = settings.home / "traces"
    if date:
        path = traces / f"{date}.jsonl"
    else:
        files = sorted(traces.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        path = files[-1] if files else traces / "none.jsonl"
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return events


def build_timeline(events: list[dict]) -> list[dict]:
    """把扁平事件列表按 turn 分组，并给每个事件算 offset_ms（距轮次起点）和
    dur_ms（距上一个事件的间隔，即该动作的耗时）。"""
    turns: list[dict] = []
    cur: dict | None = None
    for ev in events:
        t = ev.get("type")
        if t == "turn_start":
            cur = {
                "user_message": ev.get("user_message", ""),
                "start_ts": ev.get("ts"),
                "events": [],
                "end": None,
                "model": None,
                "provider": None,
            }
            turns.append(cur)
        elif t == "turn_end":
            if cur:
                cur["end"] = ev
        else:
            if cur:
                cur["events"].append(ev)
                if t == "llm":
                    cur["model"] = ev.get("model")
                    cur["provider"] = ev.get("provider")

    out = []
    for turn in turns:
        if not turn["end"]:
            continue  # 未结束的轮次（进程被杀）跳过
        start = _parse_ts(turn["start_ts"])
        end = _parse_ts(turn["end"]["ts"])
        total_ms = (end - start).total_seconds() * 1000
        prev_ts = turn["start_ts"]
        for ev in turn["events"]:
            ts = _parse_ts(ev["ts"])
            ev["offset_ms"] = round((ts - start).total_seconds() * 1000, 1)
            ev["dur_ms"] = round((ts - _parse_ts(prev_ts)).total_seconds() * 1000, 1)
            prev_ts = ev["ts"]
        out.append({
            "user_message": turn["user_message"],
            "model": turn["model"],
            "provider": turn["provider"],
            "total_ms": round(total_ms, 1),
            "iterations": turn["end"].get("iterations"),
            "reply": turn["end"].get("reply", ""),
            "events": turn["events"],
        })
    return out


@app.get("/api/trace")
def api_trace(date: str | None = None) -> dict:
    events = _load_events(date)
    print("files", list_trace_files())
    return {"turns": build_timeline(events), "files": list_trace_files(), "raw_events": events}


@app.get("/")
def index() -> HTMLResponse:
    # no-store：开发期 HTML 常改，避免浏览器用缓存的旧页面
    return HTMLResponse(_read_html(), headers={"Cache-Control": "no-store, no-cache, must-revalidate"})



_TRACE_HTML = Path(__file__).parent / "trace.html"


def _read_html() -> str:
    return _TRACE_HTML.read_text(encoding="utf-8")



if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8001)
