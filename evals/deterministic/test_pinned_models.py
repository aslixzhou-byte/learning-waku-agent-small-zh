"""确定性评估——精选模型短名单（“你的模型”）。

功能：用户跨提供方固定模型；聊天切换器只显示这份短名单，且每个提供方第一个
固定的模型就是该提供方的默认模型（切换到它时采用）。Sean 提出的真实目标：
“每个 api key 有一个默认模型，用户可以选更多模型，聊天切换器显示设置里已经
选中的模型。”

短名单存放在 .waku/models.json 中，形如 {"pinned": ["provider:model", ...]}；
这些测试驱动 dashboard 的 /api/pin 路由调用的同一批辅助函数。"""

from __future__ import annotations

import json

import pytest

from waku.ops import dashboard as d


PROVIDER_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY",
                 "MINIMAX_API_KEY", "MOONSHOT_API_KEY", "ZHIPU_API_KEY", "OPENROUTER_API_KEY",
                 "XAI_API_KEY")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """让所有 load_settings() 指向一个临时 home，并从该目录运行，使
    apply_settings 的 find_dotenv 写入临时 .env，同时清空所有提供方密钥，
    这样默认短名单为空，除非某个测试设置了一个。"""
    import os

    monkeypatch.setenv("WAKU_HOME", str(tmp_path))
    # 始终用 setenv（而非 delenv）：pytest 的 delenv(raising=False) 无法撤销
    # 之后 apply_settings 对 os.environ[k]=v 的写入——那曾让 WAKU_MODEL=kimi-k3
    # 卡在 deepseek 上并让真实评估 400。
    for var in ("WAKU_PROVIDER", "WAKU_MODEL", "WAKU_SMALL_MODEL", "WAKU_EPISODIC_STORE"):
        monkeypatch.setenv(var, os.environ.get(var, ""))
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("")
    for var in PROVIDER_KEYS:
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def test_pin_persists_and_marks_first_per_provider_default(home):
    d.pin_action({"action": "pin", "provider": "gemini", "model": "gemini-3.5-flash"})
    d.pin_action({"action": "pin", "provider": "gemini", "model": "gemini-3.5-pro"})
    info = d.pin_action({"action": "pin", "provider": "kimi", "model": "kimi-k3"})

    # 按插入顺序持久化到磁盘
    saved = json.loads((home / "models.json").read_text())["pinned"]
    assert saved == ["gemini:gemini-3.5-flash", "gemini:gemini-3.5-pro", "kimi:kimi-k3"]

    # settings_info() 会暴露短名单；每个提供方第一个固定的是默认模型
    flags = {(p["provider"], p["model"]): p["default"] for p in info["pinned"]}
    assert flags[("gemini", "gemini-3.5-flash")] is True
    assert flags[("gemini", "gemini-3.5-pro")] is False
    assert flags[("kimi", "kimi-k3")] is True


def test_default_model_for_reads_first_pinned(home):
    assert d.default_model_for("kimi") == ""          # 尚未固定任何模型
    d.pin_action({"action": "pin", "provider": "kimi", "model": "kimi-k3"})
    d.pin_action({"action": "pin", "provider": "kimi", "model": "kimi-k2.6"})
    assert d.default_model_for("kimi") == "kimi-k3"   # 第一个固定的那个


def test_make_default_moves_model_to_front_of_its_group(home):
    d.pin_action({"action": "pin", "provider": "kimi", "model": "kimi-k3"})
    d.pin_action({"action": "pin", "provider": "kimi", "model": "kimi-k2.6"})
    d.pin_action({"action": "default", "provider": "kimi", "model": "kimi-k2.6"})
    assert d.default_model_for("kimi") == "kimi-k2.6"


def test_unpin_removes_and_promotes_next_default(home):
    d.pin_action({"action": "pin", "provider": "gemini", "model": "gemini-3.5-flash"})
    d.pin_action({"action": "pin", "provider": "gemini", "model": "gemini-3.5-pro"})
    info = d.pin_action({"action": "unpin", "provider": "gemini", "model": "gemini-3.5-flash"})
    assert [p["model"] for p in info["pinned"]] == ["gemini-3.5-pro"]
    assert d.default_model_for("gemini") == "gemini-3.5-pro"   # 幸存者现在成为默认


def test_switching_provider_adopts_its_pinned_default(home, monkeypatch):
    """provider 变更时的 apply_settings 会使用该提供方固定的默认模型，
    绝不把上一个提供方的模型带到新端点（线上曾出现 kimi->gemini 404）。"""
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("MOONSHOT_API_KEY", "k")
    (home / "models.json").write_text(json.dumps({"pinned": ["kimi:kimi-k3"]}))
    # 先从 gemini + gemini 模型开始，再在未指定模型的情况下切到 kimi
    d.apply_settings({"provider": "gemini", "model": "gemini-3.5-flash", "keys": {}})
    info = d.apply_settings({"provider": "kimi", "keys": {}})
    assert info["provider"] == "kimi"
    assert info["model"] == "kimi-k3"          # 采用了固定的默认模型，而非 gemini 的模型


def test_pinned_are_grouped_by_provider_for_display(home):
    """后来添加的模型（如 claude-fable-5）应当与其提供方的其他模型列在一起，
    而不是滞留底部——同时每个提供方的默认模型（第一个固定的）保持在顶部。"""
    (home / "models.json").write_text(json.dumps({"pinned": [
        "anthropic:claude-opus-4-8", "openai:gpt-5.3-chat-latest",
        "anthropic:claude-fable-5", "openai:gpt-4.1-mini"]}))
    rows = [(r["provider"], r["model"], r["default"]) for r in d.settings_info()["pinned"]]
    assert rows == [
        ("anthropic", "claude-opus-4-8", True),      # 默认模型保持在第一位
        ("anthropic", "claude-fable-5", False),      # 与 anthropic 归为一组，不滞留
        ("openai", "gpt-5.3-chat-latest", True),
        ("openai", "gpt-4.1-mini", False),
    ]


def test_no_pins_is_empty_not_error(home):
    info = d.settings_info()
    assert info["pinned"] == []
    assert d.default_model_for("anthropic") == ""


def test_default_pair_is_flagship_then_fast(home):
    """每个提供方为切换器提供旗舰 + 快速的默认模型对。"""
    from waku.loop.models import PROVIDERS

    assert PROVIDERS["anthropic"].default_pair() == ["claude-opus-4-8", "claude-sonnet-5"]
    assert PROVIDERS["gemini"].default_pair() == ["gemini-3.1-pro-preview", "gemini-3.5-flash"]
    assert PROVIDERS["kimi"].default_pair() == ["kimi-k3", "kimi-k2.7-code-highspeed"]
    # 未设置旗舰/快速的提供方会回退到 model/small_model
    assert PROVIDERS["minimax"].default_pair() == ["MiniMax-M3", "MiniMax-M2"]


def test_defaults_apply_before_curation_and_only_for_keyed_providers(home, monkeypatch):
    """还没有 models.json -> 切换器对配置了密钥的提供方显示旗舰+快速，
    旗舰在前（因此它是默认）。没有密钥的提供方不显示（你无法使用它们）。"""
    monkeypatch.setenv("MOONSHOT_API_KEY", "k")      # 只有 kimi 配置了密钥
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")     # 以及 anthropic

    info = d.settings_info()
    pairs = [(p["provider"], p["model"], p["default"]) for p in info["pinned"]]
    assert pairs == [
        ("anthropic", "claude-opus-4-8", True), ("anthropic", "claude-sonnet-5", False),
        ("kimi", "kimi-k3", True), ("kimi", "kimi-k2.7-code-highspeed", False),
    ]
    assert d.default_model_for("kimi") == "kimi-k3"        # 旗舰模型是默认
    assert d.default_model_for("gemini") == ""            # 无密钥 -> 无默认


def test_pinning_snapshots_defaults_then_diverges(home, monkeypatch):
    """第一次固定操作会持久化计算出的默认值 + 变更，这样后续编辑不会不断
    复活默认值。"""
    monkeypatch.setenv("MOONSHOT_API_KEY", "k")      # 只有 kimi 配置了密钥 -> 2 个默认
    d.pin_action({"action": "unpin", "provider": "kimi", "model": "kimi-k2.7-code-highspeed"})
    assert (home / "models.json").exists()               # 现在已具体化
    assert [p["model"] for p in d.settings_info()["pinned"]] == ["kimi-k3"]


def test_known_catalog_providers_can_list(home):
    """防止“只有 2 个模型”的 bug：提供方从显式的 catalog_url 或
    {base_url}/models 端点（仅 openai-wire）列出模型。openai 默认没有
    base_url，因此它必须设置 catalog_url——否则选择器会回退到它硬编码的
    2 个默认模型。

    minimax/glm 走 anthropic-wire 且没有已验证的公共 /models 端点，所以它们
    有意展示精选默认模型，直到我们接入并验证一个端点。"""
    from waku.loop.models import PROVIDERS

    CAN_LIST = {"anthropic", "openai", "openrouter", "gemini", "deepseek", "kimi", "xai"}
    for name in CAN_LIST:
        prov = PROVIDERS[name]
        can_list = bool(prov.catalog_url) or (prov.kind == "openai" and bool(prov.base_url))
        assert can_list, f"{name} lost its catalog source (add catalog_url)"


def test_list_models_honors_provider_override(home, monkeypatch):
    """添加行时先选提供方，因此 list_models(provider) 必须列出该提供方的
    目录，而非当前激活的提供方。通过预置缓存来避免网络请求。"""
    import time

    from waku.loop.models import PROVIDERS

    url = PROVIDERS["kimi"].catalog_url
    # 缓存元组是 (ts, models, error)——error 为 None 表示一次真实的列取
    monkeypatch.setattr(d, "_models_cache", {url: (time.time(), [{"id": "kimi-k3"}], None)})
    out = d.list_models("kimi")
    assert out["provider"] == "kimi"
    assert out["listed"] is True
    assert [m["id"] for m in out["models"]] == ["kimi-k3"]
