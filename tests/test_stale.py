"""价目表摘要：没有价目表只是面积未知，有价目表却没记摘要才是说不清失没失效。

`uv run pytest tests/test_stale.py`
"""
from types import SimpleNamespace

from xirang_area import price


def fake(area):
    return SimpleNamespace(name="x", ip={"area": area} if area is not None else {})


def test_no_price_table_is_not_a_stale_one(monkeypatch):
    monkeypatch.setattr(price, "gen_digest", lambda pkg: "sha256:1")
    assert price.stale(fake(None)) is None


def test_a_price_table_without_its_digest_cannot_be_judged(monkeypatch):
    monkeypatch.setattr(price, "gen_digest", lambda pkg: "sha256:1")
    assert "没记生成产物摘要" in price.stale(fake({"corner": {}}))
    assert "另一份" in price.stale(fake({"corner": {"gen_digest": "sha256:0"}}))
    assert price.stale(fake({"corner": {"gen_digest": "sha256:1"}})) is None
