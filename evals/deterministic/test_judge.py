"""确定性评估——以 K3 为裁判的质量判定模块（waku.ops.judge）。

密闭环境中无法调用真实模型，因此我们用预设的 JSON 回复桩替换客户端，并固化
解析/钳制行为 + 优雅失败。要点：裁判出现故障时必须降级为 None（无分数），
绝不能让一场竞速崩溃。"""

from __future__ import annotations

from waku.ops import judge as J


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


class _Client:
    def __init__(self, text):
        self._text = text

    class _Messages:
        pass

    @property
    def messages(self):
        m = self._Messages()
        m.create = lambda **kw: _Resp(self._text)
        return m


def _stub(monkeypatch, text):
    monkeypatch.setattr(J, "get_client", lambda settings: _Client(text))


def test_parses_score_and_reason(monkeypatch):
    _stub(monkeypatch, '{"score": 8, "reason": "solid and concise"}')
    v = J.judge_reply("do X", "here is X")
    assert v["score"] == 8 and v["reason"] == "solid and concise"
    assert v["judge"] == J.JUDGE_MODEL


def test_score_is_clamped_0_10(monkeypatch):
    _stub(monkeypatch, '{"score": 99, "reason": "over"}')
    assert J.judge_reply("q", "a")["score"] == 10
    _stub(monkeypatch, '{"score": -5, "reason": "under"}')
    assert J.judge_reply("q", "a")["score"] == 0


def test_extracts_json_from_surrounding_prose(monkeypatch):
    _stub(monkeypatch, 'Sure!\n{"score": 6, "reason": "ok"}\nHope that helps')
    assert J.judge_reply("q", "a")["score"] == 6


def test_empty_reply_is_not_judged(monkeypatch):
    _stub(monkeypatch, '{"score": 5, "reason": "x"}')
    assert J.judge_reply("q", "   ") is None


def test_bad_json_degrades_to_none(monkeypatch):
    _stub(monkeypatch, "the model rambled without any json")
    assert J.judge_reply("q", "a") is None
