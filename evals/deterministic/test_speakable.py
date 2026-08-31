"""朗读内容同样是一个纯函数。TTS 引擎会把 emoji 读出声来（“火箭”“闪光”），
所以 `_speakable` 在朗读前会剔除它们（以及游离的 markdown）。来自录制的线上
bug → 在这里固化。"""

import pytest

from waku.gateway.voice import _speakable

STRIPPED = [
    ("All set! 🎉 Booked for Saturday. 🎾", "All set! Booked for Saturday."),
    ("Done ✅ — added 3 events 🚀🚀🚀", "Done — added 3 events"),
    ("Here you go 😊👍", "Here you go"),
    ("Nice 🇬🇧 flag", "Nice flag"),            # 区域指示标志
    ("**Bold** and `code` and # heading", "Bold and code and heading"),
    ("plain words, nothing to strip", "plain words, nothing to strip"),
]


@pytest.mark.parametrize("raw,expected", STRIPPED)
def test_speakable_strips_emoji_and_markdown(raw, expected):
    assert _speakable(raw) == expected


def test_speakable_handles_empty():
    assert _speakable("") == ""
    assert _speakable("💥✨🔥") == ""   # 全 emoji -> 无话可说
