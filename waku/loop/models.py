"""模型接入 —— 八个 provider，一个循环，零框架。

循环只说一种方言：Anthropic 的 Messages 形状（system/messages/tools 进，content blocks 出）。
provider 以两种方式接入：

  anthropic 线格式（原生）    → Anthropic、Kimi/Moonshot、GLM/Z.ai、MiniMax
  openai 线格式（薄适配器）   → OpenAI、Google Gemini、DeepSeek、OpenRouter

用 WAKU_PROVIDER=anthropic|openai|gemini|deepseek|minimax|kimi|glm|openrouter 选择，
并在 .env 里设置该 provider 的 API key。如果下面的默认模型 id 过时了，用
WAKU_MODEL / WAKU_SMALL_MODEL 覆盖 —— 它们只是字符串。这对 openrouter 最重要：
它是一把 key 挡在几百个模型前面，所以 WAKU_MODEL=<vendor>/<model>（例如
"google/gemini-3.5-flash"）能选你想要的任何一个 —— 而且下面它的默认值是 $0
的 ":free" id，所以完全不花钱也能用（限流）。dashboard 的 Settings 标签页会列出实时目录。
"""

from __future__ import annotations  # 让类型注解在旧版 Python 里也能用

import json                       # 把工具参数序列化成 OpenAI 线格式的 arguments
import os                         # 读取 provider 的 API key 环境变量
from dataclasses import dataclass  # dataclass：自动生成 __init__ 等样板

# SimpleNamespace 是 Python 标准库 types 模块提供的一个轻量级对象容器，让你可以用属性（.）的方式访问字典里的值。
"""
# 创建一个对象，属性名=值
obj = SimpleNamespace(name="Alex", age=25, city="Hangzhou")
# 用 . 访问属性（而不是 dic["key"]）
print(obj.name)    # "Alex"
print(obj.age)     # 25
print(obj.city)    # "Hangzhou"
"""
from types import SimpleNamespace  # 轻量命名空间：用来捏造"长得像 anthropic"的响应对象

from waku.config import Settings  # 配置：providers 靠它选 provider 和填充默认模型


@dataclass(frozen=True)  # frozen=True：字段不可变，PROVIDERS 常驻配置不许被误改
class Provider:
    kind: str        # 'anthropic' 或 'openai' —— 线格式
    key_env: str     # key 存在哪个环境变量里
    base_url: str | None  # 自定义端点（代理/兼容网关）；None 则用 SDK 默认地址
    model: str       # 默认主模型（循环用）
    small_model: str  # 默认廉价模型（检索门禁 + 记忆整合用）
    # 在哪里列出这个 provider 的模型（Settings 选择器用）。openai 线格式的
    # provider 会自动用 {base_url}/models；当某 provider 的聊天端点和目录端点
    # 不同时（例如 kimi 说 anthropic 线格式，但模型目录列在它 OpenAI 兼容的
    # API 上）才设置这个字段。上面的默认值只是起点 —— 列出的任何模型一键可换。
    catalog_url: str | None = None
    # 聊天切换器默认为这个 provider 钉住的两个模型：一个旗舰（最高质量）和一个
    # 快速（便宜/低延迟）。区别于 model/small_model —— 例如 anthropic 的循环默认
    # 是 sonnet-5，但你想展示的旗舰是 opus-4.8。留空则回退到 model/small_model。
    flagship: str = ""
    fast: str = ""

    def default_pair(self) -> list[str]:
        """[flagship, fast]，去重 —— 切换器的默认选项。"""
        pair = [self.flagship or self.model, self.fast or self.small_model]  # 钉住的旗舰/快速为空则回退到循环默认模型
        return list(dict.fromkeys(m for m in pair if m))  # dict.fromkeys 去重且保序；同时滤掉空串


PROVIDERS: dict[str, Provider] = {  # 名字 → Provider 的目录，循环的模型世界全集
    "anthropic": Provider("anthropic", "ANTHROPIC_API_KEY", None,  # Anthropic 官方，anthropic 线格式
                          "claude-sonnet-5", "claude-haiku-4-5-20251001",
                          catalog_url="https://api.anthropic.com/v1/models",
                          flagship="claude-opus-4-8", fast="claude-sonnet-5"),
    # gpt-5.6 的 REASONING 模型（luna/sol/terra）不能在 /v1/chat/completions 上
    # 用函数工具（它们需要 /v1/responses），所以 Waku 每一轮都会在它们身上 400。
    # 非 reasoning 的 "chat" 系列确实能正常调工具；gpt-5.3-chat-latest 是其中
    # 最新的一个具体模型（优先于 gpt-5-chat-latest 别名，这样基准可复现）。
    # gpt-4.1-mini 是一个便宜的、能调工具的检索门禁模型。
    # base_url 为 None（SDK 默认），这样选择器指向 OpenAI 的目录。
    "openai":    Provider("openai", "OPENAI_API_KEY", None,  # OpenAI，openai 线格式
                          "gpt-5.3-chat-latest", "gpt-4.1-mini",
                          catalog_url="https://api.openai.com/v1/models"),
    # 一把 key，各家的模型，还有一个 $0 档：下面的默认模型是免费 id（":free" 后缀）。
    # 限流（无额度约 50 请求/天）。
    "openrouter": Provider("openai", "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1",  # OpenRouter，一把 key 接各家
                           "nvidia/nemotron-3-super-120b-a12b:free",
                           "google/gemma-4-26b-a4b-it:free"),
    "gemini":    Provider("openai", "GEMINI_API_KEY",  # Google Gemini，走它 OpenAI 兼容端点
                          "https://generativelanguage.googleapis.com/v1beta/openai/",
                          "gemini-3.5-flash", "gemini-3.1-flash-lite",
                          # Google 的 Pro 档不叫 "gemini-3.5-pro"（那个 id 会 404）；
                          # 当前的 Pro 是 gemini-3.1-pro-preview。
                          flagship="gemini-3.1-pro-preview", fast="gemini-3.5-flash"),
    "deepseek":  Provider("openai", "DEEPSEEK_API_KEY", "https://api.deepseek.com",  # DeepSeek，openai 线格式
                          "deepseek-v4-pro", "deepseek-v4-pro"),
    "minimax":   Provider("anthropic", "MINIMAX_API_KEY", "https://api.minimaxi.com/anthropic",  # MiniMax，anthropic 线格式
                          "MiniMax-M3", "MiniMax-M2"),
    # K3 是旗舰默认；门禁/摘要器继续用便宜的 K2.6
    # （实时目录里没有纯 "kimi-k2.7" —— 只有 -code 变体；我们查过）。
    # 如果你的 key 只能开 K3，用 WAKU_SMALL_MODEL=kimi-k3 覆盖。
    "kimi":      Provider("anthropic", "MOONSHOT_API_KEY", "https://api.moonshot.ai/anthropic",  # Kimi/Moonshot，anthropic 线格式
                          "kimi-k3", "kimi-k2.6",
                          catalog_url="https://api.moonshot.ai/v1/models",
                          flagship="kimi-k3", fast="kimi-k2.7-code-highspeed"),
    "glm":       Provider("anthropic", "ZHIPU_API_KEY", "https://api.z.ai/api/anthropic",  # 智谱 GLM，anthropic 线格式
                          "glm-5.2", "glm-5-turbo"),
    # xAI Grok，走它 OpenAI 兼容的端点。下面的模型 id 只是起点 —— 加上 XAI_API_KEY
    # 后选择器会列出实时目录（权威来源）；钉住当前实际可用的旗舰/快速模型即可。
    "xai":       Provider("openai", "XAI_API_KEY", "https://api.x.ai/v1",  # xAI Grok，openai 线格式
                          "grok-4", "grok-4-fast",
                          catalog_url="https://api.x.ai/v1/models"),
}


def get_client(settings: Settings):
    """为 settings.provider 构建客户端，并填入默认模型 id。
    返回任何带有 .messages.create(...)（Anthropic 形状）的东西。"""
    provider = PROVIDERS.get(settings.provider)  # 按名字查目录；取不到返回 None 而非抛异常
    if provider is None:  # 拼错的 provider 名：直接终止并列出可选项
        raise SystemExit(f"Unknown WAKU_PROVIDER '{settings.provider}'. "  # 错误信息原文保留（用户可见）
                         f"Pick one of: {', '.join(PROVIDERS)}")

    # .strip() 以免复制粘贴带来的尾部换行/空格污染鉴权头
    # （header 是 latin-1 的；一个杂散的非 ASCII 字符会报出晦涩的错误）。
    api_key = (settings.api_key or os.getenv(provider.key_env, "")).strip()  # 显式 WAKU_API_KEY 优先，否则读 provider 的环境变量
    if not api_key:  # key 缺失：直接提示用户去配，而不是让 SDK 报看不懂的错
        raise SystemExit(
            f"No API key for provider '{settings.provider}'. "  # 错误信息原文保留
            f"Set {provider.key_env} in .env (see .env.example)."
        )
    try:
        api_key.encode("latin-1")  # 鉴权头是 latin-1 编码；非 ASCII 字符会直接毁掉鉴权
    except UnicodeEncodeError:  # 抓到的多是"从中文文档复制来的智能引号"这类粘贴事故
        raise SystemExit(
            f"{provider.key_env} contains a non-ASCII character (e.g. a smart quote "  # 错误信息原文保留
            f"or arrow from a bad paste). Re-paste the key with no spaces or line breaks."
        )

    settings.model = settings.model or provider.model  # 用户没显式指定模型时，填入 provider 默认
    settings.small_model = settings.small_model or provider.small_model  # 廉价模型同理（门禁/整合用）
    base_url = settings.base_url or provider.base_url  # 自定义端点优先于 provider 默认

    # 挂起的网络调用绝不能无声地冻结一轮
    timeout = float(os.getenv("WAKU_LLM_TIMEOUT", "120"))  # 超时秒数可配置，默认 120 秒

    if provider.kind == "anthropic":
        import anthropic  # 延迟导入：只有真的用到才拉 SDK

        kwargs: dict = {"api_key": api_key, "timeout": timeout}  # 原生 anthropic 客户端
        if base_url:  # 只有非默认端点时才传 base_url
            kwargs["base_url"] = base_url
        return anthropic.Anthropic(**kwargs)
    return OpenAICompatClient(api_key=api_key, base_url=base_url, timeout=timeout)  # openai 线格式走薄适配器


class OpenAICompatClient:
    """使用 Anthropic Messages 的请求格式（这是循环代码所期望的），
    但底层实际上调用的是 OpenAI 风格的 chat.completions API。
    两种线格式之间的全部差异大约只有 60 行代码——值得读一遍。"
    """

    def __init__(self, api_key: str, base_url: str | None = None, timeout: float = 120.0):
        import openai  # 延迟导入：不用 openai 线格式就不加载

        self._client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self.messages = SimpleNamespace(create=self._create, stream=self._stream)  # 伪装成 anthropic 的 messages 命名空间

    def _to_openai(self, *, model, messages, max_tokens, system=None, tools=None) -> dict:
        """把 Anthropic 形状的入参翻译成 OpenAI chat.completions 的参数。"""
        oai_messages = []
        if system:
            oai_messages.append({"role": "system", "content": system})  # OpenAI 把 system 也放进 messages 数组
        for message in messages:
            content = message["content"]
            if isinstance(content, str):  # 纯文本消息：直接照搬
                oai_messages.append({"role": message["role"], "content": content})
            elif message["role"] == "assistant":
                # anthropic content blocks → assistant 文本 + tool_calls
                text = "".join(b.text for b in content if getattr(b, "type", "") == "text")  # 拼接文本块为 assistant 正文
                calls = []
                for b in content:
                    if getattr(b, "type", "") != "tool_use":
                        continue  # 只要工具请求块，其余跳过
                    call = {"id": b.id, "type": "function",
                            "function": {"name": b.name, "arguments": json.dumps(b.input)}}  # arguments 必须序列化成字符串
                    extra = getattr(b, "extra", None)   # Gemini 的 thought_signature
                    if extra:
                        call["extra_content"] = extra  # 放回 tool_call 上，让下一轮能原样带回
                    calls.append(call)
                entry: dict = {"role": "assistant", "content": text or None}
                if calls:
                    entry["tool_calls"] = calls  # 有工具调用才挂上 tool_calls 字段
                oai_messages.append(entry)
            else:
                # anthropic tool_result blocks → 每条一个 'tool' 消息
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        oai_messages.append({  # OpenAI 要求每条工具结果单独成一条 role=tool 的消息
                            "role": "tool",
                            "tool_call_id": block["tool_use_id"],
                            "content": block["content"],
                        })

        kwargs: dict = {"model": model, "messages": oai_messages,
                        "max_completion_tokens": max_tokens}
        if tools:
            kwargs["tools"] = [  # 把 anthropic 形状的工具 schema 转成 OpenAI 的 tools 参数
                {"type": "function",
                 "function": {"name": t["name"], "description": t["description"],
                              "parameters": t["input_schema"]}}
                for t in tools
            ]
        return kwargs

    def _call(self, kwargs: dict, **extra):
        """跑 chat.completions.create，带 max_tokens 键名回退
        （老一点的 OpenAI 兼容端点只认 max_tokens，不认较新的 max_completion_tokens）。
        只有当错误确实关于那个参数时才重试 —— 对任何错误都重试会掩盖真正的故障
        （例如某次 gpt-5.x 调用会因别的原因失败，然后 max_tokens 重试把它埋在
        一条令人困惑的 'use max_completion_tokens' 消息下面）。"""
        try:
            return self._client.chat.completions.create(**kwargs, **extra)  # 先按新参数名尝试
        except Exception as exc:
            m = str(exc).lower()
            if "max_completion_tokens" not in m and "max_tokens" not in m:  # 报错与那个参数无关 → 原样抛出
                raise
            k = dict(kwargs)
            k["max_tokens"] = k.pop("max_completion_tokens", None)  # 只把参数名回退成旧的 max_tokens
            return self._client.chat.completions.create(**k, **extra)

    def _create(self, *, model, messages, max_tokens, system=None, tools=None):
        """OpenAI 的 messages.create() 实现 —— 调用并翻译回 Anthropic 形状的响应。"""
        response = self._call(self._to_openai(  # 先翻译成 OpenAI 参数再调用
            model=model, messages=messages, max_tokens=max_tokens, system=system, tools=tools))
        if not getattr(response, "choices", None):
            # 有些 OpenAI 兼容端点（例如限流时的 OpenRouter）会返回 200 但
            # 带一个错误体和空的 choices：把那条消息抛出来，而不是在下面的
            # TypeError 上挂掉。
            err = getattr(response, "error", None) or "endpoint returned no choices"
            raise RuntimeError(f"{model}: {err}")
        choice = response.choices[0].message  # OpenAI 只返回一个 choice，取它
        blocks = []
        if choice.content:
            blocks.append(SimpleNamespace(type="text", text=choice.content))  # 文本块 → anthropic 形状
        for call in choice.tool_calls or []:  # 工具调用 → anthropic 形状的 tool_use 块
            blocks.append(SimpleNamespace(
                type="tool_use", id=call.id, name=call.function.name,
                input=json.loads(call.function.arguments or "{}"),  # arguments 字符串反序列化回字典
                # Gemini 的 thinking 模型会在这里挂一个 thought_signature，并
                # 要求在下一轮把它随工具调用一起原样带回，否则后续会 400
                #（"missing a thought_signature"）。带着它，好让 _to_openai 能
                # 放回去。对其他所有 provider 都是 None。
                extra=getattr(call, "extra_content", None),
            ))
        usage = getattr(response, "usage", None)  # 用量信息可能缺失，取不到就用 0
        return SimpleNamespace(
            stop_reason="tool_use" if choice.tool_calls else "end_turn",  # 有工具调用就停在这，否则正常结束
            usage=SimpleNamespace(
                input_tokens=getattr(usage, "prompt_tokens", 0),  # OpenAI 命名 → anthropic 命名
                output_tokens=getattr(usage, "completion_tokens", 0),
            ),
            content=blocks,
        )

    def _stream(self, *, model, messages, max_tokens, system=None, tools=None):
        """在 OpenAI chat.completions 流上做 Anthropic 形状的流式 —— 和 _create
        一样的两格式桥接，只是边到边产出文本。循环在 stream=True 时使用
        （例如 dashboard 的实时聊天）。"""
        kwargs = self._to_openai(  # 复用地化翻译，剩下的交给 _OpenAIStream 处理增量
            model=model, messages=messages, max_tokens=max_tokens, system=system, tools=tools)
        return _OpenAIStream(self, kwargs)


class _OpenAIStream:
    """一个模仿 anthropic 的 messages.stream() 的上下文管理器：迭代 .text_stream
    拿到文本增量，然后用 .get_final_message() 拿到组装好的 Anthropic 形状响应
    （文本 + 重组后的工具调用 + usage）。"""

    def __init__(self, client: OpenAICompatClient, kwargs: dict):
        self._client = client  # 持有适配器，好调用底层的 _call
        self._kwargs = kwargs  # 已翻译成 OpenAI 形状的请求参数
        self._text: list[str] = []  # 累积文本增量，最后拼成完整正文
        self._tools: dict[int, dict] = {}   # 索引 → {id, name, args}；按增量片段的 index 归位
        self._usage = None  # 流末尾的用量信息（include_usage=True 时由末块携带）

    def __enter__(self):
        return self  # with 进来返回自身，便于链式调用

    def __exit__(self, *exc):
        return False  # 不吞异常，保持 with 块语义

    @property
    def text_stream(self):
        stream = self._client._call(  # 发起流式请求，要求携带 usage
            self._kwargs, stream=True, stream_options={"include_usage": True})
        for chunk in stream:
            if getattr(chunk, "usage", None):  # usage 通常在最后一个 chunk 上
                self._usage = chunk.usage
            if not chunk.choices:
                continue  # 无内容的 chunk（如仅带 usage）直接跳过
            delta = chunk.choices[0].delta
            if getattr(delta, "content", None):  # 文本增量：累积并逐段产出
                self._text.append(delta.content)
                yield delta.content
            for tc in (getattr(delta, "tool_calls", None) or []):  # 工具调用按 index 分片到达
                slot = self._tools.setdefault(tc.index, {"id": None, "name": "", "args": ""})  # 新 index 建槽，已有则复用
                if tc.id:  # id 只出现在第一个分片上
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    slot["args"] += tc.function.arguments  # arguments 是碎片，需要拼接

    def get_final_message(self):
        """组装 Anthropic 形状的完整响应（文本 + 工具调用 + usage）。"""
        blocks = []
        text = "".join(self._text)
        if text:
            blocks.append(SimpleNamespace(type="text", text=text))
        for slot in self._tools.values():  # 把拼好的工具碎片还原成 tool_use 块
            blocks.append(SimpleNamespace(
                type="tool_use", id=slot["id"], name=slot["name"],
                input=json.loads(slot["args"] or "{}")))
        usage = self._usage
        return SimpleNamespace(
            stop_reason="tool_use" if self._tools else "end_turn",  # 出现过工具调用就停在这
            usage=SimpleNamespace(
                input_tokens=getattr(usage, "prompt_tokens", 0),
                output_tokens=getattr(usage, "completion_tokens", 0)),
            content=blocks,
        )
