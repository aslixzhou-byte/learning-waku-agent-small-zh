"""程序性记忆 —— SKILL.md 文件：如何行动，只在相关时加载。

官方 Anthropic Agent Skills 格式：带 `name` 和 `description` 的 YAML frontmatter
（description 兼任触发器 —— 没有自定义 `triggers:` 字段，那是规范定型前
launch-agent-skills 用的）。

渐进式披露，这才是关键：
  1. 每个 skill 的 frontmatter 始终被扫描（开销小）
  2. 只有当 skill 与消息匹配时，其正文才被加载进提示词
  3. skill 引用的文件只在模型提出请求时才读取
"""

from __future__ import annotations  # 让类型注解（如 Skill | None）在旧版 Python 里也能用

import re  # 正则：frontmatter 切分 + 关键词匹配
from dataclasses import dataclass  # Skill 数据类：自动生成 __init__ 等样板
from pathlib import Path  # 文件路径操作


@dataclass  # 标记为数据类：一个 skill 的完整描述
class Skill:
    name: str  # 技能名（frontmatter 的 name）
    description: str  # 一句话描述（frontmatter 的 description，兼任关键词触发器）
    body: str  # SKILL.md 正文（渐进式披露：只在匹配时才进提示词）
    path: Path  # SKILL.md 在磁盘上的位置


def _parse_text(text: str, path: Path) -> Skill | None:
    """校验 SKILL.md 内容（加载器和 create_skill 工具都会用到）。"""
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)  # 切出 --- 包裹的 YAML 头与正文
    if not match:
        return None  # 没有 frontmatter 就不是合法 skill
    front, body = match.groups()
    fields = dict(  # 把 frontmatter 每行 "key: value" 解析成字典
        (k.strip(), v.strip().strip("'\""))
        for k, _, v in (line.partition(":") for line in front.splitlines() if ":" in line)
    )
    if "name" not in fields or "description" not in fields:  # 两个必填字段缺一即拒绝
        return None
    return Skill(fields["name"], fields["description"], body.strip(), path)


def _parse(path: Path) -> Skill | None:
    return _parse_text(path.read_text(encoding="utf-8"), path)  # 读文件后交给纯文本版解析


class SkillLoader:
    """扫描 skill 目录：仓库的 skills/（内置 + 社区）以及 WAKU_HOME/skills
    （已安装或由代理自己编写）。任何 SKILL.md 发生变化时自动重新扫描，
    因此会话中途创建的 skill 会在下一轮生效。"""

    def __init__(self, dirs: list[Path]):
        self.dirs = dirs  # 要扫描的目录列表
        self.skills: list[Skill] = []  # 当前已加载的全部 skill
        self._sig: tuple = ()  # 目录签名快照（路径+修改时间），用于检测变化
        self.refresh()  # 构造时立即加载一次

    def _scan_sig(self) -> tuple:
        sig = []
        for d in self.dirs:
            if d.is_dir():  # 目录可能还不存在（首次运行尚未创建）
                for f in sorted(d.rglob("SKILL.md")):  # 递归找出所有 SKILL.md，排序保证稳定
                    sig.append((str(f), f.stat().st_mtime))  # 记录 路径 + 修改时间
        return tuple(sig)

    def refresh(self) -> None:
        self.skills = []
        for d in self.dirs:
            if not d.is_dir():
                continue  # 目录不存在就跳过
            for f in sorted(d.rglob("SKILL.md")):
                skill = _parse(f)  # 解析每个 SKILL.md
                if skill:  # 校验失败就静默跳过，不中断启动
                    self.skills.append(skill)
        self._sig = self._scan_sig()  # 重载后刷新签名，让后续 match 不再重复重扫

    def match(self, message: str, max_skills: int = 2) -> list[Skill]:
        """透明触发器：消息与每个 skill 的 name+description 之间的关键词重叠。
        没有 embedding，没有魔法 —— 你可以在心里算出得分。"""
        if self._scan_sig() != self._sig:   # 有 skill 被新增/编辑 —— 重新加载
            self.refresh()
        msg_words = set(re.findall(r"[a-z0-9]{3,}", message.lower()))  # 消息分词成集合
        scored = []
        for skill in self.skills:
            skill_words = set(re.findall(r"[a-z0-9]{3,}", (skill.name + " " + skill.description).lower()))
            overlap = len(msg_words & skill_words)  # 关键词重叠数即匹配得分
            if overlap >= 2:  # 至少 2 个重叠词才认为相关，避免单词的噪音误触发
                scored.append((overlap, skill))
        scored.sort(key=lambda pair: -pair[0])  # 得分高的排前面
        return [skill for _, skill in scored[:max_skills]]  # 最多返回 max_skills 个
