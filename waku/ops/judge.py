"""K3 作为裁判的「Compare」质量评分。

完成度（waku.ops.scoring）是确定性的——有没有触发正确的工具。质量则是另一半：
*回答本身有多好*，覆盖了清单看不到的开放式部分。这里没有唯一正确答案，
所以我们按市场在这一轴上的做法（MT-Bench / Chatbot-Arena 风格）：
由一个 LLM 按评分标准/量表对对话记录打分，0-10 分 + 一行理由。

裁判必须是「没有参赛」的模型——否则它就是给自己打分，既不公平也不可信
（你没法用 K3 当裁判来测 K3）。默认是 **gpt-5.6-sol**：一个很强的推理模型，
碰巧在这里是个糟糕的*参赛者*（它在聊天端点无法调用工具），但却是很好的
*裁判*（评分是纯文本，不需要工具）。可在Compare里按场次切换，或用
WAKU_JUDGE_* 配置。任意模型提供方都行——Waku 的 OpenAI 兼容客户端暴露的
`.messages.create` 形状与 anthropic 线相同，所以裁判与提供方无关。
"""

from __future__ import annotations

import json        # 解析裁判返回的 JSON
import os          # 读取 WAKU_JUDGE_* 环境变量
import threading   # 信号量：限制并发裁判调用数
import time        # 重试退避间隔

from waku.config import Settings, load_settings
from waku.loop.models import get_client

# 裁判的模型提供方 / 模型默认值，可用 WAKU_JUDGE_PROVIDER / WAKU_JUDGE_MODEL 覆盖。
JUDGE_PROVIDER = os.getenv("WAKU_JUDGE_PROVIDER", "deepseek")
JUDGE_MODEL = os.getenv("WAKU_JUDGE_MODEL", "deepseek-v4-pro")

# 一场对比会同时给每一列打分——8 个裁判调用同时打到同一个端点，
# 会有一些被 429 拒绝，那些列就会显示「—」。限制并发运行的裁判调用数
# （在整场对比的线程间共享），免得裁判被冲垮；其余的排队，仍会被打分。
_JUDGE_SEM = threading.Semaphore(int(os.getenv("WAKU_JUDGE_CONCURRENCY", "2")))   # 最多 2 个并发

# 判分提示词（发给裁判 LLM 的评分标准/量表）：
# 角色设定——你是严格、公正的裁判，为 AI 助手的回复打分。
# 上下文——给出用户的问题、助手的回复，以及助手当轮实际运行的工具列表（作为事实依据）。
# 打分规则——0-10 分：9-10 完全命中且准确、简洁、对局限诚实；5-8 大体命中但有小缺口/凑字数/小错；
#   1-4 只答到一部分、含糊或部分错误；0 忽略请求，或声称做了列表之外根本没运行的动作。
# 关键提醒——列表中的工具确实运行过，声明与之相符不扣分；只有「幻觉」（声称做了没有对应工具调用的
#   动作）才扣分。输出格式——只回一个 JSON 对象，不要任何说明文字：
#   {"score": <0-10 的整数>, "reason": "<一句简短理由>"}
_RUBRIC = """You are a strict, fair judge scoring an AI assistant's reply.

The user asked:
{task}

The assistant replied:
{reply}
{actions}
Score how well the reply serves the user's request on a 0-10 scale:
- 9-10: fully addresses the request, correct, concise, honest about any limits.
- 5-8: mostly addresses it, minor gaps, padding, or small errors.
- 1-4: partial, vague, or partly wrong.
- 0: ignores the request, or claims an action that is NOT in the tool list above.

IMPORTANT: the tools listed above REALLY ran — this assistant can take those
actions. Do NOT penalize the reply for saying it did something that appears in
that list; those claims are true. Only "hallucinating" counts against it when it
claims an action with no matching tool call.

Reply with ONLY a JSON object, no prose:
{{"score": <int 0-10>, "reason": "<one short sentence>"}}"""


def judge_reply(task: str, reply: str, provider: str | None = None,
                model: str | None = None, tools: list | None = None) -> dict | None:
    """为一条回复打分。`tools` 是本轮实际触发的工具名列表——作为事实依据传给裁判，
    这样一句真实的「我保存了」（且列表里有 save_note）就不会被误判为幻觉。
    返回 {"score": 0-10, "reason": str, "judge": model}；如果没有可评分的对象
    或裁判不可达则返回 None（裁判的闪失绝不能毁掉一场对比）。"""
    if not (reply or "").strip():       # 空回复没有可评分的对象
        return None
    provider = provider or JUDGE_PROVIDER   # 未显式指定时用默认裁判
    model = model or JUDGE_MODEL
    # 拼进判分提示词的内容：当轮实际运行的工具（事实依据）或「本轮没运行任何工具」。
    actions = (f"\nTools the assistant actually ran this turn (ground truth): "
               f"{', '.join(tools)}.\n" if tools else
               "\nThe assistant ran no tools this turn.\n")
    prompt = _RUBRIC.format(task=task[:2000], reply=reply[:4000], actions=actions)
    # 提示词里的任务/回复都限长，避免把超大内容塞进裁判的上下文
    settings = Settings(provider=provider, model=model, small_model="",
                        home=load_settings().home, apple_calendar=False)
    # 一场对比会同时给每一列打分，所以端点会看到一阵突发流量，可能 429。
    # 只对 API 调用做重试（退避时间递增）；信号量限制了并发数。
    # 已经返回但解析不了的响应不是瞬时问题——不要在它身上浪费重试。
    resp = None
    for attempt in range(4):            # 最多尝试 4 次
        try:
            client = get_client(settings)   # 填入该 provider 的默认模型 id
            with _JUDGE_SEM:                # 借用信号量：限制并发裁判调用
                resp = client.messages.create(
                    model=settings.model, max_tokens=300,
                    messages=[{"role": "user", "content": prompt}])
            break                       # 成功拿到响应就跳出重试循环
        except Exception:
            if attempt < 3:             # 前 3 次失败才等待重试；最后一次失败直接放弃
                time.sleep(1.2 * (attempt + 1))   # 1.2s、2.4s、3.6s——让 429 消除
    if resp is None:                    # 一直失败，返回 None（调用方跳过这一列）
        return None
    try:
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        print("text:: -> {}".format(text))
        # 只取文本块；从第一个 { 到最后一个 } 截取 JSON 对象
        obj = json.loads(text[text.index("{"): text.rindex("}") + 1])
        score = max(0, min(10, int(obj["score"])))   # 把分数夹紧到 0-10
        return {"score": score, "reason": str(obj.get("reason", ""))[:200], "judge": settings.model}
    except Exception:
        return None   # 有响应，但不是合法 JSON——重试也没用
