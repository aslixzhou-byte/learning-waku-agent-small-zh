"""确定性评估——dashboard 的静态资源协同一致。

没有 JS 测试运行器（刻意不设构建步骤），因此这些廉价的检查守护着拆分式前端
静默崩溃的两种方式：
  1. index.html 引用了磁盘上不存在的 <script>/<link>。
  2. 内联的 onclick=/oninput=/… 处理器调用了任何 js/ 文件都未定义的函数
     （例如某个处理器被重命名或移动，而某个调用点遗漏了）。
两者都会渲染成一个没有报错的失效按钮——这正是无需浏览器、Python 侧检查
就能抓到的。"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "waku" / "ops" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
JS_FILES = sorted((STATIC / "js").glob("*.js"))
JS_SRC = "\n".join(f.read_text(encoding="utf-8") for f in JS_FILES)

# 内联处理器可能调用、但无需 js/ 文件定义的 JS 关键字 / 内置对象 / DOM 全局量。
# 刻意保持精简——其余任何标识符都必须是真正的应用函数。
ALLOWED = {
    "if", "for", "while", "switch", "return", "typeof", "new", "await", "function",
    "Math", "JSON", "Date", "Number", "String", "Boolean", "Object", "Array",
    "parseInt", "parseFloat", "isNaN", "console", "setTimeout", "setInterval",
    "encodeURIComponent", "decodeURIComponent", "alert", "confirm", "prompt",
    "document", "window", "event", "fetch",
}


def test_referenced_assets_exist():
    """src=/href= 中的每个 /static/... 都指向真实文件。"""
    refs = re.findall(r'(?:src|href)="(/static/[^"]+)"', INDEX)
    assert refs, "expected script/link references in index.html"
    for ref in refs:
        target = STATIC / ref[len("/static/"):]
        assert target.is_file(), f"index.html references missing asset: {ref}"


def _defined_names() -> set[str]:
    names = set()
    names |= set(re.findall(r'^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)', JS_SRC, re.M))
    names |= set(re.findall(r'^(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=', JS_SRC, re.M))
    return names


def _handler_calls(text: str) -> set[str]:
    """内联 on*=... 处理器内部被调用的函数名（不包括方法调用）。"""
    called = set()
    for body in re.findall(r'\bon\w+="([^"]*)"', text) + re.findall(r"\bon\w+='([^']*)'", text):
        # '(' 正前方、且不属于属性访问（无前导 .）的标识符
        called |= set(re.findall(r'(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(', body))
    return called


def test_inline_handlers_are_defined():
    """内联处理器调用的每个函数都在 js/ 文件中定义（或属于允许的内置对象）。
    在被当作死按钮发布前抓住重命名/移动过的处理器。扫描 index.html 以及
    js 文件生成的全部 HTML。"""
    defined = _defined_names()
    called = _handler_calls(INDEX) | _handler_calls(JS_SRC)
    missing = {n for n in called if n not in defined and n not in ALLOWED}
    assert not missing, f"inline handlers call undefined functions: {sorted(missing)}"


def test_app_js_is_gone():
    """巨石已拆分；index.html 不得再加载旧的单文件。"""
    assert not (STATIC / "app.js").exists(), "stale app.js still present"
    assert "/static/app.js" not in INDEX, "index.html still references app.js"
