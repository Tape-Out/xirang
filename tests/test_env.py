"""CI 里可选依赖必须真的装上：没装时黑盒测试走「核对不了」那条路，照样全绿。

`uv run pytest tests/test_env.py`
"""
import os

import pytest

from xirang_back import sv


@pytest.mark.skipif(not os.environ.get("CI"), reason="只在 CI 里要求")
def test_pyslang_present():
    assert sv.available(), "uv sync 要带 --all-packages --all-extras"
