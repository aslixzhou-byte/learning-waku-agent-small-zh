"""FastAPI 网关 Web 版的 cli.py，非流式，用于功能测试。
和 cli.py 做同一件事：构建一个 Waku、收一条消息、跑完整循环、返回回复。
区别只是输入/输出换成 HTTP，前端是一个内联的简单 HTML 页面。
启动：
    pip install fastapi uvicorn
    python -m waku.gateway.fastapi_gateway
    http://127.0.0.1:8000
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from waku.app import Waku  # 核心应用：会话、循环、记忆都经它

app = FastAPI(title="Waku Web")

# 懒加载：第一次请求时才构建 Waku（构建会读 .env、连 state.db、建模型客户端）。
# 这样服务总能先起来，缺 key 之类的问题在第一次请求时以错误信息返回，而不是启动即崩。
_waku: Waku | None = None

def get_waku() -> Waku:
    global _waku
    if _waku is None:
        _waku = Waku()
        _waku.session.session_id = "web"  # 独立的对话线程，和 terminal/voice/telegram 分开
    return _waku

class ChatRequest(BaseModel):
    message: str  # 用户输入的消息

@app.post("/chat")
def chat(req: ChatRequest) -> dict:
    """跑一轮非流式对话：组装工作记忆 → 循环 → 持久化，返回结构化结果。"""
    try:
        waku = get_waku()
    except SystemExit as exc:  # get_client 在缺 key / provider 配置错误时抛 SystemExit
        return {"error": str(exc)}
    result = waku.respond(req.message, source="web")
    return {
        "reply": result.reply,                 # 模型最终回复文本
        "tool_calls": result.tool_calls,       # 本轮触发的工具调用（名称/参数/输出）
        "iterations": result.iterations,       # 推理-行动循环实际跑了几轮
    }


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return HTML


HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Waku Web</title>
  <style>
    body {
      font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
      max-width: 720px;
      margin: 40px auto;
      padding: 0 16px;
      color: #1a1a1a;
      background: #fafafa;
    }
    h1 {
      font-size: 1.4rem;
      font-weight: 600;
      letter-spacing: -0.01em;
      margin-bottom: 0.25rem;
    }
    .dim {
      color: #777;
      font-size: 0.9rem;
      margin-top: 0;
      margin-bottom: 1.2rem;
    }
    .input-row {
      display: flex;
      gap: 10px;
      align-items: center;       /* 垂直居中，完美对齐 */
      margin-bottom: 8px;
    }
    textarea {
      flex: 1;
      padding: 4px 12px;         /* 上下内边距减小 */
      font-size: 14px;
      line-height: 1.4;
      border: 1px solid #d0d7de;
      border-radius: 8px;
      background: #ffffff;
      resize: none;              /* 禁止拖拽，保持固定高度 */
      height: 30px;              /* 和按钮一样高 */
      min-height: 30px;
      max-height: 30px;
      transition: border-color 0.2s, box-shadow 0.2s;
      font-family: inherit;
      box-shadow: 0 1px 2px rgba(0,0,0,0.02);
      box-sizing: border-box;    /* 确保 padding 不会撑大 */
    }
    textarea:focus {
      outline: none;
      border-color: #4f46e5;
      box-shadow: 0 0 0 3px rgba(79, 70, 229, 0.15);
    }
    .btn-send {
      flex-shrink: 0;
      padding: 0 14px;
      font-size: 12px;
      font-weight: 600;
      color: #fff;
      background: linear-gradient(145deg, #4f46e5, #3b35c9);
      border: none;
      border-radius: 8px;
      cursor: pointer;
      letter-spacing: 0.3px;
      box-shadow: 0 2px 6px rgba(79, 70, 229, 0.25);
      transition: all 0.2s ease;
      display: flex;
      align-items: center;
      justify-content: center;
      white-space: nowrap;
      height: 30px;              /* 固定高度 */
      min-height: 30px;
      line-height: 1;
    }
    .btn-send:hover {
      background: linear-gradient(145deg, #5b52f0, #3f37c9);
      box-shadow: 0 4px 12px rgba(79, 70, 229, 0.35);
      transform: translateY(-1px);
    }
    .btn-send:active {
      transform: translateY(1px) scale(0.97);
      box-shadow: 0 1px 4px rgba(79, 70, 229, 0.3);
    }
    .reply {
      margin-top: 20px;
      padding: 16px 18px;
      background: #ffffff;
      border: 1px solid #e9ecf0;
      border-radius: 12px;
      white-space: pre-wrap;
      box-shadow: 0 1px 4px rgba(0,0,0,0.02);
      line-height: 1.6;
    }
    .tools {
      margin-top: 8px;
      font-size: 0.85rem;
      color: #4b5563;
      background: #f3f4f6;
      padding: 6px 14px;
      border-radius: 20px;
      display: inline-block;
    }
    .err {
      color: #b91c1c;
      margin-top: 12px;
      font-weight: 500;
      background: #fee2e2;
      padding: 8px 14px;
      border-radius: 8px;
      display: inline-block;
    }
    .info-bar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 6px 12px;
      margin-top: 6px;
    }
    @media (max-width: 480px) {
      .input-row {
        flex-wrap: wrap;
      }
      .btn-send {
        width: 100%;
        height: 30px;
        min-height: 30px;
      }
      textarea {
        height: 30px;
        min-height: 30px;
        max-height: 30px;
      }
    }
  </style>
</head>
<body>
  <h1>Waku Web</h1>
  <p class="dim">发一条消息，看 Waku 跑完整循环（可能触发工具调用）。非流式。</p>

  <div class="input-row">
    <textarea id="msg" placeholder="比如：明天下午三点和 Alex 开个会"></textarea>
    <button class="btn-send" onclick="send()">发送</button>
  </div>

  <div id="reply" class="reply" style="display:none"></div>
  <div class="info-bar">
    <div id="tools" class="tools"></div>
    <div id="err" class="err" style="display:none"></div>
  </div>

<script>
async function send() {
  const msg = document.getElementById('msg').value.trim();
  if (!msg) return;

  const errEl = document.getElementById('err');
  const replyEl = document.getElementById('reply');
  const toolsEl = document.getElementById('tools');

  errEl.style.display = 'none';
  errEl.textContent = '';
  replyEl.style.display = 'none';
  replyEl.textContent = '';
  toolsEl.textContent = '';

  try {
    const res = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg })
    });
    const data = await res.json();

    if (data.error) {
      errEl.textContent = '错误：' + data.error;
      errEl.style.display = 'block';
      return;
    }

    replyEl.textContent = data.reply;
    replyEl.style.display = 'block';

    if (data.tool_calls && data.tool_calls.length) {
      toolsEl.textContent =
        '工具调用：' + data.tool_calls.map(c => c.tool + '(' + JSON.stringify(c.args) + ')').join('， ') +
        ' · 共 ' + data.iterations + ' 轮';
    } else {
      toolsEl.textContent = '无工具调用 · 共 ' + data.iterations + ' 轮';
    }
  } catch (e) {
    errEl.textContent = '请求失败：' + e.message;
    errEl.style.display = 'block';
  }
}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
