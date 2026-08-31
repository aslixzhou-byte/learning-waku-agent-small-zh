import os
import sys
from pathlib import Path

import pytest

# evals/ 与 waku/ 同级而非位于其内部——这样从仓库根目录运行 `pytest evals` 时两者都能被导入。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# apply_settings() 通过 os.environ[k] = v 写入这些变量（绕过了 monkeypatch）。
# 每个测试后快照并还原，避免某个 dashboard 单元测试把 WAKU_MODEL=kimi-k3
# 留在 deepseek 提供方上，影响后续的真实评估运行。
_WAKU_ENV = (
    "WAKU_PROVIDER",
    "WAKU_MODEL",
    "WAKU_SMALL_MODEL",
    "WAKU_EPISODIC_STORE",
    "WAKU_HOME",
)


@pytest.fixture(autouse=True)
def _restore_waku_env_after_test():
    snap = {k: os.environ.get(k) for k in _WAKU_ENV}
    yield
    for k, v in snap.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
