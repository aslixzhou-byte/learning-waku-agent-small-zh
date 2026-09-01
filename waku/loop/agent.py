"""主循环 观察 → 推理 → 行动 → 重复。这个文件就是全部的核心。
每个 agent 框架本质上都是这个 while 循环，只是套了更多间接层：
    while not done:
        response = llm(messages, tools)          # 推理
        if response asks for tools:
            results = run(tool_calls)            # 行动
            messages += results                  # 观察
        else:
            done                                 # 回复人类
循环结束护栏（架构图橙色框的退出条件）：
  1. 模型不再请求工具  → 本轮自然结束
  2. 达到 max_iterations → 硬性停止，绝不无限空转
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable
import anthropic

from waku.tools.registry import ToolRegistry

# 观察者让网关能实时展示工具调用，也让 ops/tracing 记录它们, 两者都不必接入循环逻辑本身。
LoopEvent = dict[str, Any]
Observer = Callable[[str, LoopEvent], None]

@dataclass
class LoopResult:
    reply: str  # 最终回复给人类的文本（未完成时是护栏提示语）
    tool_calls: list[LoopEvent] = field(default_factory=list)  # 本轮执行的工具调用记录（展示/追踪用）
    iterations: int = 0  # 实际跑了几轮推理-行动循环（上限 max_iterations）

def run_loop(
    client: anthropic.Anthropic,         # LLM 客户端（原生 anthropic，或 OpenAI 兼容适配器）
    model: str,                          # 主模型 id（provider 默认或 WAKU_MODEL 覆盖）
    system: str,                         # 系统提示词（人设 + 记忆 + 当前时间等）
    messages: list[dict],                # 对话历史；会被原地修改以记录本轮全过程
    tools: ToolRegistry,                 # 工具注册表：把 schema 给模型，把调用安全执行
    max_iterations: int = 10,            # 护栏 2：推理-行动循环上限，防止无限空转
    max_tokens: int = 2048,              # 单次 LLM 调用的输出 token 上限
    observer: Observer | None = None,    # 观察者回调：实时看到文本增量/工具调用/LLM 事件（可空）
    stream: bool = False,                # 为 True 时逐 token 流式发出助手文本（dashboard 用）
) -> LoopResult:
    """运行一轮 agent。`messages` 会被原地修改 调用结束后，它包含本轮完整的
    工作记忆（助手的思考、工具调用、工具结果），这正是被追踪的内容。
    stream=True 会在助手文本生成的同时逐字发出（notify("text", {"delta": ...})），
    让网关能逐 token 展示它，供 dashboard 使用。对不支持流式的客户端则回退为单次调用。"""

    print("into run_loop: messages={}".format(messages))
    print("into run_loop: tools={}".format(tools._tools.keys()))
    print("into run_loop: max_iterations={}".format(max_iterations))
    print("into run_loop: max_tokens={}".format(max_tokens))

    notify = observer or (lambda kind, ev: None)  # 没有观察者就垫一个空操作，避免各处判空
    result = LoopResult(reply="")                 # 结果以空回复起步，循环过程中逐步填充
    can_stream = stream and hasattr(client.messages, "stream")  # 既请求了流式、客户端又支持时才用流式

    for iteration in range(1, max_iterations + 1):  # 从 1 数到上限（含），保证至少能跑一轮
        result.iterations = iteration                # 记录当前轮次；超限停时停在 max_iterations

        print("now loop iteration: {}".format(iteration))
        print("messages=")
        for i, msg in enumerate(messages):
            print(f"  [{i}] role: {msg['role']}, content: {msg['content']}")
        print("system=".format(system))

        # ---- 推理：用当前工作记忆发起一次 LLM 调用
        response = None  # 先置空；流式失败时靠它判断是否需要回退
        if can_stream:
            try:
                with client.messages.stream(          # 流式上下文管理器：边生成边产出文本增量
                    model=model, system=system, messages=messages,
                    tools=tools.schemas(), max_tokens=max_tokens,
                ) as s:
                    for delta in s.text_stream:       # 逐段增量转发给观察者（dashboard 逐字展示）
                        notify("text", {"delta": delta})
                    response = s.get_final_message()  # 流结束后组装出完整的响应对象
            except Exception:
                response = None  # 任何流式故障 → 回退为单次调用
        if response is None:  # 没请求流式，或流式失败：走普通的一次性调用
            response = client.messages.create(
                model=model,
                system=system,
                messages=messages,
                tools=tools.schemas(),  # 工具 schema 列表：告诉模型有哪些工具可用、参数长什么样
                max_tokens=max_tokens,
            )
        print("now iteration notify msg: ")
        print(f"iteration: {iteration}, stop_reason: {response.stop_reason}, "
              f"input_tokens: {response.usage.input_tokens}, "
              f"output_tokens: {response.usage.output_tokens}")

        notify("llm", {"iteration": iteration, "stop_reason": response.stop_reason,  # 观察：上报停止原因与 token 用量
                       "usage": {"in": response.usage.input_tokens, "out": response.usage.output_tokens}})

        # 助手的本轮回复（文本和/或工具请求）并入工作记忆
        print("messages.append: 助手本轮回复并入工作记忆")
        messages.append({"role": "assistant", "content": response.content})  # 追加 assistant 消息，下一轮推理可见

        tool_uses = [b for b in response.content if b.type == "tool_use"]  # 从内容块里筛出工具请求（其余是文本块）

        # ---- 护栏 1：没有工具调用 → 模型在直接回复人类
        if not tool_uses:
            result.reply = "".join(b.text for b in response.content if b.type == "text")  # 把文本块拼成最终回复

            print("run loop not tool uses -> return result: {}, iter: {}".format(result.reply, result.iterations))

            return result  # 自然结束：模型不再请求工具，本轮到此为止

        # ---- 行动：执行每个被请求的工具；观察：把结果喂回
        tool_results = []  # 收集各工具的结果，稍后一并作为 user 消息追加
        print("run loop tool uses: ")

        for call in tool_uses:
            output = tools.execute(call.name, call.input)  # 安全执行：工具内部报错也只会返回文本而非抛异常
            event = {"tool": call.name, "args": call.input, "output": output}  # 结构化事件，供展示/追踪
            result.tool_calls.append(event)  # 记进结果，调用方（dashboard 等）可据此展示

            print(f"To notify: Tool: {event['tool']}, Args: {event['args']}, Output: {event['output']}")

            notify("tool", event)            # 观察者实时看到这次工具调用
            tool_results.append(
                {"type": "tool_result", "tool_use_id": call.id, "content": output}  # Anthropic 线格式：把结果绑回对应调用
            )

        print("messages.append: 本轮工具执行结果消息追加")
        messages.append({"role": "user", "content": tool_results})  # 观察：工具结果并入工作记忆，下一轮可读

    # ---- 护栏 2：迭代次数耗尽，硬性停止（提示语原文保留，直接呈现给用户）
    result.reply = "(I hit my iteration limit before finishing — try breaking the request into smaller steps.)"
    print("run loop stop error -> return result: {}, iter: {}".format(result.reply, result.iterations))
    return result  # 超限即停，把提示语作为最终回复返回
