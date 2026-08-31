"""Skill 安装器 —— 一条命令就能尝试安装任意人的 skill。

    python -m waku skill install https://github.com/<user>/<repo>/blob/main/skills/foo/SKILL.md
    python -m waku skill install https://gist.github.com/<user>/<id>

下载 SKILL.md，校验 frontmatter（与 CI 对社区 PR 执行的检查相同），
并放入 WAKU_HOME/skills/<name>/，加载器会在下次启动时拾取它。
Skill 就是 markdown —— 安装前请阅读你装的内容。
"""

from __future__ import annotations  # 让类型注解在旧版 Python 里也能用

import urllib.request  # 标准库 HTTP 下载（不引入第三方依赖）

from waku.config import load_settings  # 读取 WAKU_HOME 等配置
from waku.memory.procedural.loader import _parse  # 复用加载器的 frontmatter 校验


def _raw_url(url: str) -> str:
    """把常见的 GitHub/Gist 页面 URL 转换为原始内容 URL。"""
    if "github.com" in url and "/blob/" in url:  # GitHub blob 页面 → raw.githubusercontent 直链
        return url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
    if "gist.github.com" in url and not url.endswith("/raw"):  # Gist 页面 → 末尾补 /raw 变直链
        return url.rstrip("/") + "/raw"
    return url  # 已经是可下载的 URL（或以 /raw 结尾）就原样返回


def install(url: str) -> None:
    raw = _raw_url(url)
    print(f"Fetching {raw}")  # 先打印目标地址，让用户清楚在下载什么
    with urllib.request.urlopen(raw, timeout=15) as response:  # noqa: S310 — 用户提供的 URL，属设计使然
        text = response.read().decode("utf-8", errors="replace")  # 读到字节后按 UTF-8 解码，坏字节用 � 替换

    settings = load_settings()
    settings.ensure_home()  # 确保 WAKU_HOME 目录存在
    tmp = settings.home / "skills" / "_incoming" / "SKILL.md"  # 先落临时目录，校验通过再原子挪走
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(text, encoding="utf-8")

    skill = _parse(tmp)  # 校验 frontmatter（与 CI 对社区 PR 执行的检查相同）
    if skill is None:
        tmp.unlink()  # 校验失败就清掉临时文件，不留垃圾
        raise SystemExit(  # 以错误信息退出，不用堆栈
            "Invalid skill: SKILL.md needs YAML frontmatter with `name` and `description`. "
            "See skills/TEMPLATE.md in the repo."
        )

    dest = settings.home / "skills" / skill.name / "SKILL.md"  # 目标位置：按 skill 名分目录
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp.rename(dest)  # 原子移动：临时文件 → 最终位置
    tmp.parent.rmdir()  # 清掉现在已空的 _incoming 目录
    print(f"Installed '{skill.name}' → {dest}")
    print(f"  {skill.description}")
    print("It loads next time Waku starts. Read it first — skills are instructions.")
