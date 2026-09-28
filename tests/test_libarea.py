"""库包的面积只算这颗芯片依赖到的。

从前把搜索路径上所有库包的基础面积都加进合计：一颗 uart 背着三万多 µm²，
一个没有价目表的黑盒报 31,231。

`uv run pytest tests/test_libarea.py`
"""
from types import SimpleNamespace

from xirang_area import price
from xirang_core import lock
from xirang_core.model import Resolved


def lib(base):
    return SimpleNamespace(is_library=True, is_assembly=False, ip={"area": {"base": base}})


def test_only_libraries_in_the_closure_count(monkeypatch):
    pkgs = {"top": SimpleNamespace(is_library=False, is_assembly=False, ip={}),
            "used": lib(100.0), "unused": lib(30000.0)}
    monkeypatch.setattr(price, "price", lambda p, vals, lift=True: (p.ip["area"]["base"], {}))
    monkeypatch.setattr(lock, "resolve_deps", lambda top, idx: {"top": top, "used": idx["used"]})
    res = Resolved(top="top", bus="apb4", instances=[])
    assert price.annotate(res, pkgs).area_um2 == 100.0
