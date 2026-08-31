"""语音网关 —— 对笔记本说话，它回话。

    pip install -e '.[voice]'
    make voice

按键通话 MVP：按回车、说话、再按回车。你的语音会走与打字文本
完全相同的循环/记忆/评估流水线——网关只负责文字的进出
（这正是网关盒子的意义所在）。

  ears    faster-whisper（本地 Whisper，首次运行下载约 74MB 模型）
  voice   macOS `say`，默认英式发音（零配置），或已安装的神经
          Kokoro 语音：pip install kokoro soundfile，
          然后设置 WAKU_TTS=kokoro（WAKU_VOICE=bm_george / bm_fable / ...）

唤醒词模式（"hey <name>, ..."）刻意留到 v2——见 docs/roadmap：
openWakeWord 可以为这个项目命名的任何东西训练自定义唤醒词。
"""

from __future__ import annotations  # 让类型注解（如 str | None）在旧版 Python 里也能用

import os  # 读环境变量：模型、语音、阈值、唤醒词等均可调
import re  # 正则：清洗朗读文本、挑选 macOS 语音、归一化唤醒词
import subprocess  # 调用外部命令（macOS `say`）
import sys  # 判断平台（darwin）并用无缓冲输出画状态行

from waku.app import Waku  # 核心应用入口
from waku.gateway.cli import _observer  # 语音模式下也显示门禁/工具行

SAMPLE_RATE = 16000  # 16kHz：Whisper 的标准输入采样率，麦克风/流/分块都用它


def record_until_enter():
    """采集两次回车之间的麦克风音频；返回一个 float32 数组。"""
    import numpy as np  # 音频以 numpy 数组承载
    import sounddevice as sd  # 跨平台麦克风库（懒加载：属语音 extra）

    frames: list[np.ndarray] = []  # 暂存各分块音频

    def collect(indata, frame_count, time_info, status):
        # sd.InputStream 的按块回调：每来一块数据就调用一次
        frames.append(indata.copy())  # 必须 copy：回调返回后缓冲区会被复用改写

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=collect):  # 单声道、float32、回调模式采块
        input("recording — press Enter when done… ")  # 阻塞在这里，直到用户按回车结束录音
    if not frames:  # 一块都没采到（按下回车过快）
        return np.zeros(0, dtype="float32")  # 返回空数组，让调用方按静音处理
    return np.concatenate(frames)[:, 0]  # 拼接成一条长音频，再取第 0 列：多块堆叠多出维度，抽平回单声道


class Ears:
    def __init__(self, model_size: str | None = None):
        from faster_whisper import WhisperModel  # 懒加载：whisper 属语音 extra

        self.model = WhisperModel(
            model_size or os.getenv("WAKU_WHISPER_MODEL", "base"),  # 优先级：显式参数 > 环境变量 > 默认 "base"
            compute_type="int8",  # int8 量化推理：省内存、加快本地解码，精度损失可接受
        )

    def transcribe(self, audio, language: str | None = None) -> str:
        segments, _ = self.model.transcribe(
            audio, language=language or os.getenv("WAKU_WHISPER_LANG")  # 显式语言优先；为 None 则让 Whisper 自动检测
        )
        return " ".join(seg.text.strip() for seg in segments).strip()  # 各段文本拼成一句，去掉首尾空白


def _best_say_voice() -> str:
    """挑选可用的最佳 macOS 语音：优先选择已下载的 Premium/Enhanced
    （接近 Siri 的音质）语音，然后是可用的紧凑型语音。胜过硬编码一个
    机械感的默认值——而且在系统设置 ▸ 辅助功能 ▸ 朗读内容 ▸ 系统语音
    里下载更好的语音后，它会自动升级。"""
    try:
        # `say -v ?` 列出系统全部已装语音；非 macOS 或调用超时都会抛异常
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=5).stdout
    except Exception:  # noqa: BLE001
        return "Daniel"  # `say` 不可用（非 macOS / 超时）→ 回退硬编码默认
    # 每行格式："Name (variant)      en_US    # sample" —— 从区域列切分出名称
    english = []
    for ln in out.splitlines():
        m = re.match(r"^(.+?)\s{2,}([a-z]{2})_", ln)  # 语音名（非贪婪）后跟双空格再加区域码
        if m and m.group(2) == "en":  # 只保留英语区域（en_US / en_GB / en_AU…）
            english.append(m.group(1).strip())  # 收下语音名，去掉首尾空白
    # 首先：一个已下载的高质量语音（Premium > Enhanced）
    for tag in ("(Premium)", "(Enhanced)"):  # 按质量优先级依次找
        hit = next((n for n in english if tag in n), None)  # 取第一个含该标记的语音
        if hit:
            return hit
    # 否则：一个合理的紧凑型语音（如果有的话）
    for pref in ("Serena", "Kate", "Daniel", "Samantha"):  # 候选按听感从好到差排序
        if pref in english:
            return pref
    return english[0] if english else "Daniel"  # 兜底：取第一个英语语音，实在没有再用默认


# 表情符号和象形符号——TTS 引擎会把它们大声读出来（"rocket"、"sparkles"），
# 听起来很荒谬。在朗读前把它们（以及残留的 markdown 项目符号）去掉。
_EMOJI = re.compile(
    "[\U0001f300-\U0001faff"  # 符号、象形图、表情、交通、补充符号
    "\U00002600-\U000027bf"    # 杂项符号 + 装饰符
    "\U0001f1e6-\U0001f1ff"    # 区域指示符旗帜字母
    "\U00002190-\U000021ff"    # 箭头
    "\U00002b00-\U00002bff"    # 星星、杂项符号与箭头
    "\U0000fe00-\U0000fe0f"    # 变体选择器
    "\U0000200d\U000020e3\U0000fe0f]+"  # ZWJ + 键帽/变体连接符
)


def _speakable(text: str) -> str:
    """实际会被朗读的内容：无表情符号、无杂散的 markdown 标记、整齐的空格。"""
    if not text:  # 空文本直接原样返回
        return ""
    text = _EMOJI.sub("", text)  # 先剥掉表情符号（TTS 会把它们读成词）
    text = re.sub(r"[*_`#>]", "", text)      # markdown 强调/标题/引用/代码标记
    text = re.sub(r"[ \t]{2,}", " ", text)   # 收拢被移除字形留下的空隙
    return "\n".join(ln.strip() for ln in text.splitlines()).strip()  # 每行去首尾空白再逐行合并


class Mouth:
    """TTS，带一个朴素而可靠的默认（macOS `say`）和神经升级
    （Kokoro-82M，Apache-2.0 —— 它的 bm_* 语音是地道的英式管家）。"""

    def __init__(self):
        self.engine = os.getenv("WAKU_TTS", "").strip().lower()  # 显式指定引擎，小写归一化
        self.voice = os.getenv("WAKU_VOICE", "")  # 显式指定语音
        if not self.engine:
            # 自动：如果安装了神经语音（Kokoro）就用它，否则回退到
            # macOS `say`。所以单独 `pip install kokoro soundfile`
            # 就能升级语音——无需环境变量。
            try:
                import kokoro  # noqa: F401  # 探测是否已安装
                self.engine = "kokoro"
            except ImportError:
                self.engine = "say"
        if self.engine == "kokoro":
            from kokoro import KPipeline  # 神经 TTS 管线
            self.pipeline = KPipeline(lang_code="b")  # b = 英式英语
            self.voice = self.voice or "bm_george"  # 未指定时用默认管家嗓音
        elif self.engine == "say" and not self.voice:
            self.voice = _best_say_voice()  # 自动升级到 Premium/Enhanced 语音

    def speak(self, text: str) -> None:
        text = _speakable(text)  # 先清洗成可朗读文本
        if not text:  # 清洗后为空（比如整段都是表情）就闭嘴
            return
        if self.engine == "kokoro":
            import sounddevice as sd  # 本地播放合成音频
            for _, _, audio in self.pipeline(text, voice=self.voice):  # 管线按句分批产出音频
                sd.play(audio, 24000)  # Kokoro 输出 24kHz 采样率
                sd.wait()  # 播完本段再放下一段，保证句子顺序
        elif sys.platform == "darwin":
            subprocess.run(["say", "-v", self.voice or "Daniel", text], check=False)  # macOS 自带语音合成；check=False 报错也不中断
        else:
            print("(no TTS engine on this platform — set WAKU_TTS=kokoro)")  # 非 macOS 且无 Kokoro：只能提示用户


def matches_wake(text: str, wake_word: str) -> bool:
    """转录文本里是否包含（可自定义的）唤醒词？

    刻意模糊匹配：Whisper 会把 "waku waku" 听成 "wakuwaku"、"Waku, waku!"、
    "walku waku" —— 或转写成日文假名 わくわく（第一次实测就是这样！）。
    所以：`wake_word` 接受跨文字的逗号分隔变体
    （"waku waku,わくわく"），归一化会保留假名和 CJK，
    匹配用子串 + 滑动窗口相似度。纯函数 → 确定性评估。
    """
    import difflib  # 用 SequenceMatcher 计算序列相似度，做模糊匹配
    import re

    def norm(s: str) -> str:
        # 保留拉丁字母、数字、平假名/片假名（぀-ヿ）、CJK 汉字（一-鿿）
        return re.sub(r"[^a-z0-9぀-ヿ一-鿿 ]", "", s.lower()).strip()  # 小写 + 剥掉标点（保留空格供分词）

    heard = norm(text)
    if not heard:  # 全被剥光（纯标点/纯表情）：不可能命中
        return False

    for variant in (v for v in (norm(v) for v in wake_word.split(",")) if v):  # 遍历逗号分隔的每个变体（空变体跳过）
        if variant in heard or variant.replace(" ", "") in heard.replace(" ", ""):  # 直接子串匹配，另配无空格版兼容 "wakuwaku"
            return True
        words, n = heard.split(), len(variant.split())  # 按空格分词；n = 变体词数，决定窗口宽度
        if any(
            difflib.SequenceMatcher(None, " ".join(words[i : i + n]), variant).ratio() >= 0.7  # 窗口与变体相似度 ≥ 0.7 即算命中
            for i in range(max(0, len(words) - n + 1))  # 窗口滑过所有能完整取到 n 词的位置
        ):
            return True
    return False


def _mic_threshold() -> float:
    """RMS 低于此值 = 静音。麦克风差异很大——用 WAKU_MIC_THRESHOLD
    调节（如果它一直听不到你就调低，如果被环境噪声唤醒就调高）。"""
    return float(os.getenv("WAKU_MIC_THRESHOLD", "0.005"))  # 环境变量可调；默认 0.005 对多数麦克风够用


def record_command(stream, max_seconds: float = 15.0, silence_after: float = 1.2):
    """唤醒词之后：持续读取同一个流，直到说话者安静下来。
    复用同一个流很关键——每个阶段都新开一个 macOS 音频流
    正是第一个版本卡死的原因。"""
    import numpy as np

    block = SAMPLE_RATE // 10  # 100 毫秒块：音量判定与循环都用它做粒度
    frames, quiet, spoke = [], 0, False  # quiet=连续静音块计数；spoke=是否出过声
    for _ in range(int(max_seconds * 10)):  # 最多采 max_seconds 秒（按 100ms 步进）
        data, _ = stream.read(block)
        frames.append(data.copy())
        loud = float(np.sqrt((data**2).mean())) > _mic_threshold() * 2  # RMS 超过静音阈值 2 倍才算「响」
        spoke = spoke or loud  # 出过声就一直记为 True（用于确定何时开始计静音）
        quiet = 0 if loud else quiet + 1  # 响则清零静音计数，静则累加
        if spoke and quiet >= int(silence_after * 10):  # 已出声且安静满 silence_after 秒
            break  # 提前收尾，把尾部静音剪掉
    return np.concatenate(frames)[:, 0]  # 拼成单声道长音频返回


def wait_for_speech(stream, timeout: float) -> bool:
    """轮询同一个流，等待语音开始，最长 `timeout` 秒。
    麦克风一响就返回 True，一直安静则返回 False——让对话
    在无需唤醒词的情况下保持开放以便追问（Siri 风格）。"""
    import numpy as np

    block = SAMPLE_RATE // 10
    for _ in range(int(timeout * 10)):  # 按 100ms 粒度轮询到超时
        data, _ = stream.read(block)
        if float(np.sqrt((data**2).mean())) > _mic_threshold() * 2:  # 音量一过阈值即视为开口
            return True
    return False  # 超时仍无声


def wake_loop(waku: Waku, mouth: "Mouth", wake_word: str) -> None:
    """始终监听模式：用极小的 Whisper 模型以约 2.5 秒的窗口扫描麦克风，
    直到唤醒词出现，然后交给大模型。

    这是让任意短语成为唤醒词的透明、零训练方式。
    与真正的唤醒词引擎（openWakeWord）相比的取舍：多花一点 CPU，
    且分块边界偶尔会把短语切开——所以要说清楚。

    第一次实测卡死后的工程笔记：
    - 一切共用 ONE 个持久的 InputStream。每个分块都 sd.rec()+sd.wait()
      会每 2.5 秒重新打开设备，当 macOS 音频路由变化时可能永久阻塞
      （say/AirPods 等）。
    - 扫描器始终显示心跳，所以"监听中"从不会看起来"死了"。
    - Waku 说完后会排空麦克风缓冲区，这样它不会被自己声音的
      尾部唤醒（就是追踪里的 "mm-hmm" 自触发）。
    """
    import numpy as np
    import sounddevice as sd

    scout = Ears(model_size="tiny")  # 便宜、始终开启
    ears = Ears()                    # 精确、仅在唤醒后
    ack = os.getenv("WAKU_WAKE_ACK", "Yes?")  # 唤醒应答语，可自定义
    followup = float(os.getenv("WAKU_FOLLOWUP_SECONDS", "8"))  # 保持开放，Siri 风格
    block = SAMPLE_RATE // 10
    # 把侦察模型的转写语言固定为与唤醒词文字一致——
    # 否则 Whisper 会听到 "waku waku" 并"好心"写成 わくわく，
    # 而拉丁字母的唤醒词永远匹配不上。唤醒后的命令仍会自动检测语言。
    wake_lang = os.getenv("WAKU_WAKE_LANG") or ("en" if wake_word.isascii() else None)  # 唤醒词全拉丁字符则固定 en，含 CJK 则自动检测
    print(f'Listening for "{wake_word}" — Ctrl-C to quit.')

    def status(msg: str) -> None:
        sys.stdout.write(f"\r\x1b[2m{msg[:72]:<72}\x1b[0m")  # \r 回行首 + ANSI 淡色码，72 字符左对齐防闪烁残留
        sys.stdout.flush()  # 立即刷出，否则 \r 不会实时更新

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=block) as stream:  # 全程共用同一个持久输入流——重新开流正是第一版卡死的根因

        def drain() -> None:
            # 排空麦克风缓冲区：丢弃积压采样，避免把正从扬声器播出的回应录进来
            while stream.read_available >= block:
                stream.read(block)

        window: list = []  # 滚动窗口：累积约 2.5 秒的音频块做一次转写
        while True:
            data, _ = stream.read(block)
            window.append(data.copy())
            if len(window) < 25:  # 累积 2.5 秒
                continue
            chunk = np.concatenate(window)[:, 0]
            window = window[-5:]  # 保留 0.5 秒尾部，让短语可以跨分块

            if float(np.sqrt((chunk**2).mean())) < _mic_threshold():  # 本窗口 RMS 低于静音阈值（环境噪声）
                status("· listening…")
                continue  # 安静就跳过转写，省 CPU
            heard_scan = scout.transcribe(chunk, language=wake_lang)  # 用侦察模型（tiny）转写窗口
            if not matches_wake(heard_scan, wake_word):
                status(f'· heard: "{heard_scan}"' if heard_scan else "· listening…")
                if heard_scan:  # 近似命中要进追踪（用于唤醒词调优！）
                    waku.tracer.event("wake_scan", {"heard": heard_scan, "matched": False})
                continue

            print("\n[wake word]")
            mouth.speak(ack)  # 出声应答，示意「我听到了」
            drain()  # 不要把正从麦克风播放的回应转写进来

            # 唤醒后留在对话里：回答，然后在 `followup` 秒内继续监听
            # 追问——无需再喊一遍 "waku waku"（像 Siri 一样）。
            # 安静一段时间后就回到唤醒词模式。
            while True:
                heard = ears.transcribe(record_command(stream))  # 采到说完话为止，再用精确模型转写
                if heard:
                    print(f"you › {heard}")
                    result = waku.respond(heard, observer=_observer, source="voice")  # 语音命令与打字走同一条循环
                    print(f"waku › {result.reply}")
                    mouth.speak(result.reply)  # 语音播报回复
                else:
                    print("(didn't catch that)")  # 转写为空：没听清，不送进循环
                drain()  # ...并且不要被回复的尾部唤醒
                status(f"· still here — just talk, or I'll rest in {followup:.0f}s")
                if not wait_for_speech(stream, followup):  # followup 秒内没人再开口
                    break  # 安静 → 回到监听唤醒词
            window = []  # 清空滚动窗口，下一轮重新累积


def main() -> None:
    try:
        import sounddevice  # noqa: F401  # 探测可选依赖是否装好
    except ImportError:
        raise SystemExit("Voice extra not installed: pip install -e '.[voice]'")  # 没装则给出安装指引

    waku = Waku()  # 启动核心应用
    waku.session.session_id = "voice"   # 收件箱里它自己的对话线程
    mouth = Mouth()  # 初始化 TTS 引擎（自动选 kokoro / say）

    # 默认免提：始终监听 "waku waku"。默认值已经包含了小扫描模型
    # 可能听错的各种形式（wakuwaku / waka waka / 假名），所以能可靠触发。
    # 设置 WAKU_WAKE_WORD="" 则改为按键通话。
    wake_word = os.getenv(
        "WAKU_WAKE_WORD", "waku waku,wakuwaku,waku,waka waka,wako wako,walk walk,わくわく"  # 逗号分隔的变体，覆盖常见误听
    ).strip()
    if wake_word:
        try:
            wake_loop(waku, mouth, wake_word)
        except KeyboardInterrupt:  # Ctrl-C 是唤醒模式的正常退出方式
            pass
        print("\nbye — your memory stays in state.db")
        return

    ears = Ears()  # 按键通话模式：走默认模型
    print("Voice Waku ready. Press Enter to talk, Ctrl-C to quit.")
    while True:  # 按键通话主循环
        try:
            input("\npress Enter to talk… ")
            audio = record_until_enter()
        except (EOFError, KeyboardInterrupt):  # Ctrl-D / Ctrl-C：优雅退出
            break
        if audio.size < SAMPLE_RATE // 4:  # 不足 250 毫秒——大概是误触
            print("(too short, try again)")
            continue

        heard = ears.transcribe(audio)
        if not heard:
            print("(didn't catch that)")
            continue
        print(f"you › {heard}")

        result = waku.respond(heard, observer=_observer, source="voice")
        print(f"waku › {result.reply}")
        mouth.speak(result.reply)  # 语音播报模型回复

    print("bye — your memory stays in state.db")


if __name__ == "__main__":
    main()
