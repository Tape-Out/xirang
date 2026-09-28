"""装配也走矩阵：`test.axes` 写扫哪几条实例旋钮，每条照叶子 auto 的规则取值。

判据照规范八之二：删掉一条轴，只属于它的点必须消失；轴写到不存在的实例或旋钮
要报错；每一点的覆盖赢过实例的 `with:`，来源记成那一点的名字；点号键落不到
实例上一律报错，不静默丢掉。

`uv run pytest tests/test_asmmatrix.py`
"""
import pathlib

import pytest
import yaml

from xirang_core.manifest import Bad, Pkg
from xirang_core.matrix import points
from xirang_core.resolve import resolve

BASE = {"version": "0.1.0", "spec": "0.1", "kind": "ip", "lang": "bsv",
        "contract": {"version": 1, "ctrl": {"shape": "flat", "aw": 8, "dw": 32}}}


def put(root: pathlib.Path, name: str, **ip) -> None:
    (root / name / "hwsrc").mkdir(parents=True, exist_ok=True)
    (root / name / "hwsrc/A.bsv").write_text("package A; endpackage", encoding="utf-8")
    (root / name / "ip.yaml").write_text(
        yaml.safe_dump({**BASE, "name": name, **ip}, allow_unicode=True), encoding="utf-8")


def tree(root: pathlib.Path, test: dict | None = None, **extra) -> dict[str, Pkg]:
    put(root, "core", features={"m": {"default": False}})
    put(root, "ram", params={"w": {"type": "int", "range": [4, 16], "default": 8}})
    insts = [{"name": "cpu", "of": "core", "with": {"m": True}},
             {"name": "mem", "of": "ram", "addr": 0x1000}]
    put(root, "soc", instances=insts, **({"test": test} if test else {}), **extra)
    return {n: Pkg(root / n) for n in ("core", "ram", "soc")}


def test_axes_derive_the_leaf_rule_per_axis(tmp_path):
    idx = tree(tmp_path, {"axes": ["cpu.m", "mem.w"]})
    got = {lbl: ov for lbl, ov, _ in points(idx["soc"], idx)}
    assert got["Default"] == {}
    assert got["CpuMOff"] == {"cpu.m": False}
    assert got["MemW16"] == {"mem.w": 16}
    assert {"cpu.m": False, "mem.w": 4} in got.values()
    assert {"cpu.m": True, "mem.w": 16} in got.values()


def test_dropping_an_axis_drops_its_points(tmp_path):
    idx = tree(tmp_path, {"axes": ["cpu.m", "mem.w"]})
    both = {lbl for lbl, _, _ in points(idx["soc"], idx)}
    idx = tree(tmp_path, {"axes": ["cpu.m"]})
    one = {lbl for lbl, _, _ in points(idx["soc"], idx)}
    assert {"MemW4", "MemW16"} <= both and not any("MemW" in x for x in one)
    idx = tree(tmp_path)
    assert [lbl for lbl, _, _ in points(idx["soc"], idx)] == ["Default"], "不写 axes 只跑默认那一点"


def test_bad_axes_are_refused(tmp_path):
    for axes, why in ((["gpu.m"], "不是 soc 的实例"), (["cpu.x"], "不是 core 的旋钮"),
                      (["m"], "实例.旋钮")):
        idx = tree(tmp_path, {"axes": axes})
        with pytest.raises(Bad, match=why):
            points(idx["soc"], idx)
    idx = tree(tmp_path, {"axes": ["cpu.m"], "extra": [{"mem.q": 1}]})
    with pytest.raises(Bad, match="不是 ram 的旋钮"):
        points(idx["soc"], idx)
    put(tmp_path, "lone", features={"m": {"default": False}}, test={"axes": ["m"]})
    with pytest.raises(Bad, match="只给装配用"):
        points(Pkg(tmp_path / "lone"))


def test_a_point_overrides_with_and_says_where_from(tmp_path):
    tree(tmp_path)
    res = resolve("soc", [tmp_path], over={"cpu.m": False}, over_from="测试点 CpuMOff")
    cpu = next(i for _, i in res.walk() if i.name == "cpu")
    assert cpu.values["m"].value is False, "覆盖要赢过实例的 with:"
    assert "测试点 CpuMOff" in cpu.values["m"].winner.origin


def test_dotted_keys_that_miss_an_instance_are_errors(tmp_path):
    tree(tmp_path)
    with pytest.raises(Bad, match="没有实例"):
        resolve("soc", [tmp_path], over={"gpu.m": True})
    put(tmp_path, "top", instances=[{"name": "s", "of": "soc", "with": {"nope.w": 4}}])
    with pytest.raises(Bad, match="没有实例"):
        resolve("top", [tmp_path])
    with pytest.raises(Bad, match="不是装配"):
        resolve("core", [tmp_path], over={"cpu.m": True})


def test_long_labels_are_capped_but_stay_distinct():
    from xirang_core.matrix import LABEL_CAP, label
    a = {f"knob{i:02d}": True for i in range(31)}
    b = {**a, "knob30": False}
    assert len(label(a)) <= LABEL_CAP and len(label(b)) <= LABEL_CAP, "三十一个旋钮的上界当目录名超过 255 字节"
    assert label(a) != label(b) and label(a) == label(dict(a))
    assert label({"cpu.mul": False}) == "CpuMulOff", "短的照旧"
