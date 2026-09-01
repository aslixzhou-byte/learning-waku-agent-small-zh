"""Skill 安装器 一条命令就能尝试安装任意人的 skill。
    python -m waku skill install https://github.com/<user>/<repo>/blob/main/skills/foo/SKILL.md
    python -m waku skill install https://gist.github.com/<user>/<id>
下载 SKILL.md，校验 frontmatter（与 CI 对社区 PR 执行的检查相同），
并放入 WAKU_HOME/skills/<name>/，加载器会在下次启动时拾取它。
"""

from __future__ import annotations
import urllib.request
from waku.config import load_settings
from waku.memory.procedural.loader import _parse


def _raw_url(url: str) -> str:
    """把常见的 GitHub/Gist 页面 URL 转换为原始内容 URL。"""
    if "github.com" in url and "/blob/" in url:
        return url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
    if "gist.github.com" in url and not url.endswith("/raw"):
        return url.rstrip("/") + "/raw"
    return url


def install(url: str) -> None:
    raw = _raw_url(url)
    print(f"Fetching {raw}")
    with urllib.request.urlopen(raw, timeout=15) as response:
        text = response.read().decode("utf-8", errors="replace")

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
