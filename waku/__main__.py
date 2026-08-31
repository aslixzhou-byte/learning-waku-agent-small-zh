"""入口 —— 安装为 `waku` 命令（也支持 `python -m waku`）：

  waku                       在终端里对话（默认）
  waku dashboard             浏览器驾驶舱 → localhost:7777（若已配置则含 Telegram）
  waku voice                 用语音对话（需要 [voice] 额外依赖）
  waku telegram              手机 → 电脑（需要 TELEGRAM_BOT_TOKEN）
  waku brief                 晨间简报（日历 + 邮件 + 记忆）
  waku skill install <url>   安装社区技能
"""

from __future__ import annotations  # 让类型注解在旧版 Python 里也能用

import sys  # 读取命令行参数、退出进程


def main() -> None:
    args = sys.argv[1:]  # 去掉程序名后的剩余参数（第一个就是子命令）
    if not args:  # 无参数 → 默认进终端聊天
        from waku.gateway.cli import main as cli_main  # 延迟导入：只加载用到的子命令模块

        cli_main()
    elif args[0] == "dashboard":  # 浏览器驾驶舱 → localhost:7777
        from waku.ops.dashboard import main as dash_main

        dash_main()

    # elif args[0] == "voice":  # 语音对话（需要 [voice] 额外依赖）
    #     from waku.gateway.voice import main as voice_main
    #
    #     voice_main()
    # elif args[0] == "telegram":  # 手机 → 电脑（需要 TELEGRAM_BOT_TOKEN）
    #     from waku.gateway.telegram import main as tg_main
    #
    #     tg_main()

    elif args[0] == "brief":  # 晨间简报（日历 + 邮件 + 记忆）
        from waku.ops.brief import main as brief_main

        brief_main()
    elif args[0] == "skill" and len(args) >= 3 and args[1] == "install":  # 安装社区技能：waku skill install <url>
        from waku.memory.procedural.installer import install

        install(args[2])  # 第三个参数是技能 URL
    else:  # 未知子命令 → 打印用法并带错误码退出
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":  # 作为脚本直接运行（python -m waku）时的入口
    main()
