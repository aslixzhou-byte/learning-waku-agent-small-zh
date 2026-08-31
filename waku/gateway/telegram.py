"""Telegram 网关 —— 从手机给你的笔记本电脑发消息。

设置（2 分钟，免费）：
  1. 在 Telegram 里给 @BotFather 发消息 → /newbot → 复制 token
  2. 把 TELEGRAM_BOT_TOKEN=... 写入 .env
  3. 可选：设置 TELEGRAM_ALLOWED_USER=<你的数字 id>（给 @userinfobot 发消息
     获取它），这样只有你能和你的 Waku 对话
  4. make telegram

长轮询：你的笔记本调用 Telegram 的 API——无需公网 URL、无需 webhook、
无需服务器。这就是为什么爱好者的助手选 Telegram 而不是 WhatsApp
（Meta 的 Cloud API 需要企业认证和公网 HTTPS 端点）。
"""

from __future__ import annotations  # 让类型注解（如 str | None）在旧版 Python 里也能用

import os  # 读环境变量：token 与允许的用户 id

from waku.app import Waku  # 核心应用入口
from waku.gateway.cli import _observer  # 在笔记本终端上镜像门禁/工具活动


def _build_app(token: str, allowed: str):
    """构建轮询应用 + 消息处理器。由独立网关和 `waku dashboard`
    启动的后台轮询器共用。"""
    from telegram import Update  # 懒加载：telegram 是可选依赖，不该在 import 期就强制安装
    from telegram.ext import Application, ContextTypes, MessageHandler, filters  # PTB 核心：应用、上下文类型、消息处理器、过滤器

    waku = Waku()  # 每次构建新建实例（前台网关与后台轮询器互不干扰）
    waku.session.session_id = "telegram"   # 收件箱里它自己的对话线程

    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # 消息处理器：PTB 对每条匹配的消息调用一次
        if allowed and str(update.effective_user.id) != allowed:  # 配置了白名单，且发信人不是本人
            await update.message.reply_text("This Waku serves someone else. Run your own!")  # 礼貌拒绝陌生人
            return
        print(f"you › {update.message.text}")  # 在笔记本终端镜像收到的消息
        result = waku.respond(update.message.text, observer=_observer, source="telegram")  # 送入循环，source 标记来源
        print(f"waku › {result.reply}")
        await update.message.reply_text(result.reply or "(no reply)")  # 模型没产出内容时回占位文案

    app = Application.builder().token(token).build()  # 用 bot token 构建应用
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))  # 只接普通文本，忽略 /command，避免回声
    return app


def main() -> None:
    try:
        import telegram  # noqa: F401  # 探测可选依赖是否装好
    except ImportError:
        raise SystemExit("Telegram extra not installed: pip install 'waku-agent[telegram]'")  # 没装则给出安装指引

    from waku.config import load_settings  # 读取 .env 里的配置

    token = load_settings().telegram_token
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env (message @BotFather to create a bot).")  # 缺 token 直接退出
    app = _build_app(token, os.getenv("TELEGRAM_ALLOWED_USER", ""))  # 未设白名单则默认放行所有人
    print("Waku is listening on Telegram — message your bot. Ctrl-C to stop.")
    app.run_polling()  # 阻塞：靠长轮询持续从 Telegram 拉取更新


def start_in_background() -> bool:
    """在守护线程上启动 Telegram 轮询器——这样 `waku dashboard` 能用一条命令
    同时运行浏览器驾驶舱和 Telegram。已启动返回 True，没有 token 或
    未安装可选依赖时（安静地）返回 False。绝不抛异常：网关的问题
    不能拖垮仪表板。"""
    from waku.config import load_settings

    token = load_settings().telegram_token
    if not token:
        return False  # 没配 token：安静跳过，不打扰仪表板
    try:
        import telegram  # noqa: F401  # 懒依赖探测
    except ImportError:
        print("(telegram) TELEGRAM_BOT_TOKEN is set but the extra isn't installed — "
              "pip install 'waku-agent[telegram]'")
        return False

    import asyncio  # 后台线程需要自己的事件循环
    import threading  # 轮询器放守护线程，不阻塞仪表板

    allowed = os.getenv("TELEGRAM_ALLOWED_USER", "")

    import logging  # 压制 PTB 自带日志的噪音

    warned = {"conflict": False}  # 可变 dict 作开关：回调里要写它，闭包可改到

    def on_poll_error(exc: Exception) -> None:
        # 在每次轮询出错时运行。常见的是 Conflict：另一个机器人实例
        # 已经在轮询这个 token。只说一次，说清楚，并且绝不把
        # traceback 倾倒进仪表板终端。
        from telegram.error import Conflict  # 迟导入：只在报错路径才需要

        if isinstance(exc, Conflict) and not warned["conflict"]:  # 只处理 Conflict，且只提醒一次
            warned["conflict"] = True  # 置标志，防止每轮轮询失败都刷屏
            print("(telegram) another instance is already running this bot — the dashboard's "
                  "Telegram stays idle. Stop the other `waku telegram` and restart to use it here.")

    def run() -> None:
        # 不让 PTB 自己的错误日志进入仪表板终端；我们只通过 on_poll_error
        # 干净地报告那个唯一重要的错误（Conflict）。
        logging.getLogger("telegram").setLevel(logging.CRITICAL)  # 只留致命错误
        logging.getLogger("httpx").setLevel(logging.WARNING)  # 底层 HTTP 库同样压到警告级
        # 在这个线程上有自己的事件循环；start_polling 非阻塞，之后
        # run_forever 让它保持存活，直到进程（一个守护线程）退出。
        loop = asyncio.new_event_loop()  # 每个线程必须各建一个事件循环
        asyncio.set_event_loop(loop)  # 设为当前线程默认，后续 await 才找得到它
        try:
            app = _build_app(token, allowed)
            loop.run_until_complete(app.initialize())  # 异步启动步骤一：初始化应用
            loop.run_until_complete(app.start())       # 步骤二：启动应用
            loop.run_until_complete(app.updater.start_polling(error_callback=on_poll_error))  # 步骤三：开始轮询并挂上错误回调
            loop.run_forever()  # 让事件循环持续运行
        except Exception as exc:  # noqa: BLE001 — isolate the dashboard from bot errors
            print(f"(telegram) background poller stopped: {exc}")  # 打一行就返回，绝不抛出异常拖垮仪表板

    threading.Thread(target=run, daemon=True, name="telegram-poll").start()  # 守护线程：随主进程退出一起结束
    return True


if __name__ == "__main__":
    main()
