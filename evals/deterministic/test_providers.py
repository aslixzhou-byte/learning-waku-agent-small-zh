"""离线 provider 表检查：每条 PROVIDERS 记录都必须构建正确的客户端、填充其
默认模型 id，并被 dashboard 的定价与模型列取回退逻辑覆盖。无网络、无真实
密钥（通过 monkeypatch 使用假密钥）。

源于一次线上回归排查：新增一个提供方会触及共享路径
（get_client、HAS_KEY、/api/models、PRICING），而没有任何离线手段证明其余
五个仍然可用。现在有了。"""

from __future__ import annotations

import anthropic
import pytest

from waku.config import Settings
from waku.loop.models import PROVIDERS, OpenAICompatClient, get_client


@pytest.fixture(autouse=True)
def fake_keys(monkeypatch):
    for provider in PROVIDERS.values():
        monkeypatch.setenv(provider.key_env, "fake-key-for-tests")
    # 游离的自定义端点覆盖不得泄漏到这些检查中
    monkeypatch.delenv("WAKU_API_KEY", raising=False)
    monkeypatch.delenv("WAKU_BASE_URL", raising=False)


@pytest.mark.parametrize("name", list(PROVIDERS))
def test_get_client_builds_the_right_wire(name):
    provider = PROVIDERS[name]
    settings = Settings(provider=name, model="", small_model="", api_key="", base_url=None)
    client = get_client(settings)
    expected = anthropic.Anthropic if provider.kind == "anthropic" else OpenAICompatClient
    assert isinstance(client, expected)
    # 必须填好默认值，这样循环永远不会发送 model=""
    assert settings.model == provider.model
    assert settings.small_model == provider.small_model


@pytest.mark.parametrize("name", list(PROVIDERS))
def test_missing_key_exits_with_the_key_name(name, monkeypatch):
    monkeypatch.delenv(PROVIDERS[name].key_env, raising=False)
    settings = Settings(provider=name, model="", small_model="", api_key="", base_url=None)
    with pytest.raises(SystemExit, match=PROVIDERS[name].key_env):
        get_client(settings)


def test_unknown_provider_names_the_choices():
    settings = Settings(provider="not-a-provider", model="", small_model="",
                        api_key="", base_url=None)
    with pytest.raises(SystemExit, match="openrouter"):
        get_client(settings)


@pytest.mark.parametrize("name", list(PROVIDERS))
def test_dashboard_pricing_covers_every_provider(name):
    from waku.ops.dashboard import PRICING

    assert name in PRICING


@pytest.mark.parametrize("name", [n for n, p in PROVIDERS.items()
                                  if p.catalog_url is None
                                  and (p.kind == "anthropic" or not p.base_url)])
def test_model_listing_falls_back_without_a_catalog(name, monkeypatch):
    """没有可列取目录的提供方仍会给选择器提供默认模型
    （而且从不为获取它们发起网络调用）。"""
    from waku.ops import dashboard

    monkeypatch.setenv("WAKU_PROVIDER", name)
    monkeypatch.delenv("WAKU_MODEL", raising=False)
    monkeypatch.delenv("WAKU_SMALL_MODEL", raising=False)
    result = dashboard.list_models()
    assert result["listed"] is False
    ids = [m["id"] for m in result["models"]]
    assert PROVIDERS[name].model in ids
    # 旗舰（展示）模型也被提供，不只是循环默认模型
    if PROVIDERS[name].flagship:
        assert PROVIDERS[name].flagship in ids


def test_bad_key_gives_a_fixable_error_not_a_codec_crash(monkeypatch):
    """含游离的非 latin-1 字符的密钥（误粘贴的箭头/智能引号）绝不能以晦涩的
    编解码错误让整个目录崩溃——它应当返回可修复的提示信息，并且仍提供旗舰模型，
    这样 opus-4.8/fable-5 不会丢失。
    （回归：克隆的仓库其 ANTHROPIC_API_KEY 含一个 '→'，导致选择器掉到两个默认值
    并报 'latin-1 codec' 错误。）"""
    from waku.ops import dashboard

    monkeypatch.setenv("WAKU_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "x" * 100 + "→bad")
    monkeypatch.delenv("WAKU_MODEL", raising=False)
    dashboard._models_cache.clear()
    result = dashboard.list_models("anthropic")
    assert result["listed"] is False
    assert "ANTHROPIC_API_KEY" in result["error"] and "non-ASCII" in result["error"]
    assert "claude-opus-4-8" in [m["id"] for m in result["models"]]


def test_catalog_url_is_used_with_both_auth_styles(monkeypatch):
    """kimi 在 anthropic 线路上聊天，但通过其 OpenAI 兼容端点列取模型——
    catalog_url 必须优先，同时携带两种认证头样式，这样选择器提供真实的菜单
    而不是两个硬编码的默认值。"""
    import io
    import json
    import urllib.request

    from waku.ops import dashboard

    captured = {}

    def fake_urlopen(req, timeout=10):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        body = io.BytesIO(json.dumps(
            {"data": [{"id": "kimi-k3"}, {"id": "kimi-k2.7"}, {"id": "kimi-k1.5"}]}
        ).encode())
        body.__enter__ = lambda *a: body
        body.__exit__ = lambda *a: None
        return body

    monkeypatch.setenv("WAKU_PROVIDER", "kimi")
    monkeypatch.setenv("MOONSHOT_API_KEY", "fake-key-for-tests")
    monkeypatch.delenv("WAKU_MODEL", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    dashboard._models_cache.clear()

    result = dashboard.list_models()
    assert captured["url"] == PROVIDERS["kimi"].catalog_url
    assert captured["headers"]["authorization"] == "Bearer fake-key-for-tests"
    assert captured["headers"]["x-api-key"] == "fake-key-for-tests"
    assert result["listed"] is True
    assert "kimi-k3" in [m["id"] for m in result["models"]]
    dashboard._models_cache.clear()


def test_price_for_layers_model_over_provider():
    """账单正确性：kimi-k3 的运行必须按 K3 的 $3/$15 计价，而不是按 kimi
    提供方的 K2.7 费率——未知模型仍会回退到提供方估算值。
    （实时目录与 :free 路径已在上方覆盖。）"""
    from waku.ops.dashboard import MODEL_PRICING, PRICING, price_for

    assert price_for("kimi", "kimi-k3") == MODEL_PRICING["kimi-k3"] == (3.0, 15.0)
    assert price_for("kimi", "kimi-k2.7") == (0.95, 4.0)
    assert price_for("kimi", "some-future-model") == PRICING["kimi"]
    assert price_for("openrouter", "whatever:free") == (0.0, 0.0)

    # 回归：同一提供方内部，模型价格差异巨大——fable-5 定价 $10/$50，约为
    # opus 的 $5/$25 的两倍。此前提供方级别的回退让 fable-5 在记分牌上看起来
    # 比 opus 更便宜；每个模型必须携带自己的单价。
    assert price_for("anthropic", "claude-fable-5") == (10.0, 50.0)
    assert price_for("anthropic", "claude-opus-4-8") == (5.0, 25.0)
    fable_in, fable_out = price_for("anthropic", "claude-fable-5")
    opus_in, opus_out = price_for("anthropic", "claude-opus-4-8")
    assert fable_in > opus_in and fable_out > opus_out   # fable 绝不更便宜
