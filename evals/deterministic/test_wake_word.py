"""唤醒词匹配器是一个纯函数——因此它获得确定性评估。
Whisper 以可预测的方式扭曲短语；这些用例固化这种模糊匹配。"""

import pytest

from waku.gateway.voice import matches_wake

SHOULD_WAKE = [
    ("waku waku", "waku waku"),
    ("Waku, waku!", "waku waku"),            # 标点
    ("wakuwaku", "waku waku"),               # whisper 丢失了空格
    ("so anyway waku waku schedule it", "waku waku"),  # 嵌在语音中
    ("walku waku", "waku waku"),             # 一个字母的扭曲 → 模糊匹配
    ("Hey Waku", "hey waku"),
    ("hey computer, what's up", "hey computer"),
    # 来自第一次真实会话的回归：whisper 用假名写出了唤醒词——
    # 逗号后的变体覆盖其他书写体系
    ("わくわく", "waku waku,わくわく"),
    ("わくわくわく", "waku waku,わくわく"),
    ("小助手你好", "waku waku,小助手"),
]

SHOULD_NOT_WAKE = [
    ("what a nice day", "waku waku"),
    ("wake up call at nine", "waku waku"),
    ("", "waku waku"),
    ("waku waku", ""),                        # 未配置唤醒词
    ("walk to work", "waku waku"),
]


@pytest.mark.parametrize("heard,wake", SHOULD_WAKE, ids=[h for h, _ in SHOULD_WAKE])
def test_wakes(heard, wake):
    assert matches_wake(heard, wake)


@pytest.mark.parametrize("heard,wake", SHOULD_NOT_WAKE, ids=[h or "empty" for h, _ in SHOULD_NOT_WAKE])
def test_stays_asleep(heard, wake):
    assert not matches_wake(heard, wake)
